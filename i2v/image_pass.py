"""Image-pass module — the heart of Phase 1.

Two public entry points:
  - `run_image_pass(input_image_path, pass_spec, output_dir, ...)` — runs ONE
    image-to-image pass via fal. Pure library function. Used by both the
    pipeline and the CLI.
  - `run_image_pipeline(slot, input_image_path, output_dir, ...)` — runs the
    full chain of passes for one slot, threading each pass's output into the
    next as required by `source` (input_photo vs previous_pass).

Both entry points return strongly typed `ImagePassResult` / `ImagePipelineResult`
objects (see i2v.types) and write a `metadata.json` next to the images so any
run is fully reproducible.

This module is deliberately runnable as a script too:

    python -m i2v.image_pass \
        --input inputs/aaron-kitchen.jpg \
        --prompt "Transform this photo into a cinematic editorial image..." \
        --model fal-ai/nano-banana/edit \
        --output-dir outputs/quick-test \
        --label editorial_enhance

That single-pass mode is the lowest-friction way to iterate on prompts.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import httpx

from i2v import fal_client
from i2v.models import ModelAdapter, resolve_model
from i2v.types import (
    ImagePass,
    ImagePassResult,
    ImagePipelineResult,
    Slot,
    Template,
    get_slot,
    load_template,
)


# ─── Internal helpers ────────────────────────────────────────────────────────


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _ensure_output_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _output_extension(parameters: dict[str, Any]) -> str:
    fmt = str(parameters.get("output_format", "png")).lower()
    if fmt not in ("png", "jpeg", "jpg", "webp"):
        return "png"
    return "jpg" if fmt == "jpeg" else fmt


def _download(url: str, dest: Path, timeout: float = 60.0) -> None:
    """Download a URL to a local file. Streams to disk to avoid memory blowups."""
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def _write_metadata(output_dir: Path, payload: dict[str, Any]) -> None:
    """Write/merge metadata.json. If the file exists, merge passes by index."""
    meta_path = output_dir / "metadata.json"
    existing: dict[str, Any] = {}
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
    # Shallow merge top-level keys, append passes
    merged = {**existing, **payload}
    if "passes" in existing or "passes" in payload:
        merged["passes"] = (existing.get("passes", []) or []) + (payload.get("passes", []) or [])
    meta_path.write_text(json.dumps(merged, indent=2, default=str))


# ─── run_image_pass — single pass ────────────────────────────────────────────


# Suffix appended to the prompt whenever a pass has reference photos. Tells the
# model which image is the primary edit target and that the others are
# architecture-only references. Worded model-agnostically — works for Nano
# Banana edit (which sees images as a list) and would also be accepted by
# Seedance's @Image1 tag system if we ever route an image-edit through it.
_ROLE_ASSIGNMENT_SUFFIX = (
    "\n\nARCHITECTURE REFERENCES:\n"
    "The first input image is the PRIMARY subject for editing — preserve its "
    "viewpoint, framing, composition, and the position of every visible feature. "
    "The additional input images show the same room from different angles — use "
    "them ONLY as architecture references to preserve walls, cabinets, fixtures, "
    "windows, doors, materials, and overall layout. Do not introduce any "
    "architectural feature not visible in any reference image. Do not blend, "
    "transition between, or morph toward the reference angles. The output must "
    "remain in the viewpoint of the first input image."
)


def _resolve_references(
    pass_spec: ImagePass,
    cli_overrides: list[str] | None,
) -> list[Path]:
    """Pick the effective reference photos for this pass.

    Precedence:
      1. CLI override (cli_overrides) — when set, replaces template references.
      2. Template-declared references (pass_spec.reference_photos).

    Result is capped at pass_spec.max_references. Paths are resolved to
    absolute and validated to exist.
    """
    raw = cli_overrides if cli_overrides is not None else list(pass_spec.reference_photos)
    if pass_spec.max_references == 0:
        return []
    capped = raw[: pass_spec.max_references]
    resolved: list[Path] = []
    for r in capped:
        p = Path(r).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Reference image not found: {p}")
        resolved.append(p)
    return resolved


def run_image_pass(
    input_image_path: str | Path,
    pass_spec: ImagePass,
    output_dir: str | Path,
    *,
    pass_index: int = 1,
    pass_label: str | None = None,
    with_logs: bool = False,
    model_override: str | None = None,
    reference_photos_override: list[str] | None = None,
) -> ImagePassResult:
    """Run a single image-to-image pass.

    Args:
        input_image_path: local path to the PRIMARY input image (jpg/png/webp/heic).
            This is the edit target — the model produces a modified version of it.
        pass_spec: the ImagePass spec from the template.
        output_dir: directory to write the output image and metadata into.
            Created if it doesn't exist.
        pass_index: 1-based index of this pass within the chain. Determines the
            output filename (`pass_01_<label>.png`).
        pass_label: override the label used in the output filename. Falls back
            to pass_spec.label.
        with_logs: stream fal logs to stdout while the job runs.
        model_override: ignore pass_spec.model and use this model id instead.
            Useful for A/B'ing different image models on the same prompt.
        reference_photos_override: when set, replaces pass_spec.reference_photos
            for this run. Used by the CLI's --references flag so the user can
            try multi-reference without editing the template.

    Returns:
        ImagePassResult with the local output path, the generation URL, the
        full prompt + parameters used (including the role-assignment suffix
        when references were active), and the wall-clock duration.
    """
    in_path = Path(input_image_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input image not found: {in_path}")

    out_dir = _ensure_output_dir(output_dir)
    label = pass_label or pass_spec.label

    model_id = model_override or pass_spec.model
    adapter: ModelAdapter = resolve_model(model_id)

    # 1. Resolve references and build the upload list.
    refs = _resolve_references(pass_spec, reference_photos_override)
    has_refs = len(refs) > 0

    # 2. Upload primary first (it's image 1 / the edit target), then references.
    print(f"  [pass {pass_index}] uploading primary {in_path.name} to fal...")
    primary_url = fal_client.upload_local_file(in_path)
    image_urls: list[str] = [primary_url]
    for ref in refs:
        print(f"  [pass {pass_index}] uploading reference {ref.name} to fal...")
        image_urls.append(fal_client.upload_local_file(ref))

    # 3. Build model arguments. If multi-image isn't supported by this model
    #    adapter (e.g. FLUX Kontext currently takes a single image), warn and
    #    drop refs — single-image still works.
    effective_prompt = pass_spec.prompt + (_ROLE_ASSIGNMENT_SUFFIX if has_refs else "")
    if has_refs and "flux-pro/kontext" in model_id:
        # FLUX Kontext (current fal route) takes one image_url; references
        # would be silently dropped. Be explicit about it instead of guessing.
        print(
            f"  [pass {pass_index}] WARN: model {model_id} does not accept multi-image "
            f"input — dropping {len(refs)} reference(s). Switch to fal-ai/nano-banana/edit "
            f"to use references."
        )
        image_urls = image_urls[:1]
        effective_prompt = pass_spec.prompt
        refs = []
        has_refs = False

    arguments = adapter.build_arguments(image_urls, effective_prompt, pass_spec.parameters)
    if pass_spec.negative_prompt:
        arguments["negative_prompt"] = pass_spec.negative_prompt

    print(
        f"  [pass {pass_index}] running model={model_id} "
        f"refs={len(refs)} prompt={pass_spec.prompt[:80]!r}..."
    )

    # 4. Submit synchronously.
    started = time.time()
    response = fal_client.subscribe(model_id, arguments, with_logs=with_logs)
    duration = time.time() - started

    # 5. Pull output URL and download.
    output_url = adapter.extract_output_url(response)
    ext = _output_extension(pass_spec.parameters)
    out_filename = f"pass_{pass_index:02d}_{label}.{ext}"
    out_path = out_dir / out_filename
    print(f"  [pass {pass_index}] downloading result → {out_path}")
    _download(output_url, out_path)

    result = ImagePassResult(
        pass_index=pass_index,
        pass_label=label,
        model=model_id,
        prompt=effective_prompt,
        parameters=dict(pass_spec.parameters),
        input_path=str(in_path),
        reference_paths=[str(r) for r in refs],
        output_path=str(out_path),
        output_url=output_url,
        duration_sec=round(duration, 2),
        raw_response={"images_count": len(response.get("images", []) or [])},
    )

    # 6. Append to metadata.json so this pass is auditable.
    _write_metadata(
        out_dir,
        {
            "passes": [result.model_dump()],
        },
    )

    print(f"  [pass {pass_index}] done in {duration:.1f}s")
    return result


# ─── run_image_pipeline — full chain for a slot ──────────────────────────────


def run_image_pipeline(
    slot: Slot,
    input_image_path: str | Path,
    output_root: str | Path = "outputs",
    *,
    template_id: str = "ad-hoc",
    with_logs: bool = False,
    model_override: str | None = None,
    reference_photos_override: list[str] | None = None,
) -> ImagePipelineResult:
    """Run a slot's full multi-pass image pipeline.

    The chain semantics:
      - Each pass declares `source = "input_photo" | "previous_pass"`.
      - The first pass typically reads the original photo; subsequent passes
        usually chain off the prior pass's output.
      - If a pass declares `source = "input_photo"` deeper in the chain, that
        pass also reads from the original — useful for "two parallel
        treatments of the same source" patterns later (not used in v1).

    Args:
        slot: the Slot whose image_pipeline to run.
        input_image_path: original user photo.
        output_root: root directory; a per-run subdirectory is created.
        template_id: stamped into metadata for audit.
        with_logs: stream fal logs to stdout.
        model_override: when set, every pass ignores its own model and uses this
            model instead. For A/B testing models across the chain.
        reference_photos_override: when set, replaces template-declared
            reference_photos for ALL passes whose source is 'input_photo'.
            Passes whose source is 'previous_pass' are unaffected — they only
            see the prior output anyway. CLI override semantics.

    Returns:
        ImagePipelineResult with all pass outputs and a final_output_path
        property pointing at the last pass — that's the frame that feeds the
        video pass.
    """
    run_id = f"{_timestamp()}_{slot.id}_{uuid.uuid4().hex[:6]}"
    out_dir = _ensure_output_dir(Path(output_root) / run_id)

    # Stamp top-level metadata first (passes are appended as they finish)
    _write_metadata(
        out_dir,
        {
            "run_id": run_id,
            "template_id": template_id,
            "slot_id": slot.id,
            "slot_label": slot.label,
            "input_image_path": str(Path(input_image_path).resolve()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model_override": model_override,
        },
    )

    # Save a copy of the source photo into the run dir for reference
    src_copy = out_dir / f"00_source{Path(input_image_path).suffix.lower()}"
    if not src_copy.exists():
        shutil.copy2(input_image_path, src_copy)

    pass_results: list[ImagePassResult] = []
    prev_output_path: Path | None = None

    for idx, pass_spec in enumerate(slot.image_pipeline, start=1):
        # Resolve which file feeds this pass.
        if pass_spec.source == "input_photo":
            this_input = Path(input_image_path)
        elif pass_spec.source == "previous_pass":
            if prev_output_path is None:
                raise ValueError(
                    f"Pass {idx} ('{pass_spec.label}') declares source='previous_pass' but "
                    "no prior pass exists. The first pass must use source='input_photo'."
                )
            this_input = prev_output_path
        else:  # exhaustive — pydantic constrains the type
            raise AssertionError(f"Unknown pass source: {pass_spec.source}")

        # CLI reference override only applies to passes that read the
        # original photo. Chained passes (source='previous_pass') don't
        # need architecture refs — their input is already the graded primary.
        refs_for_this_pass = (
            reference_photos_override if pass_spec.source == "input_photo" else None
        )

        result = run_image_pass(
            this_input,
            pass_spec,
            output_dir=out_dir,
            pass_index=idx,
            pass_label=pass_spec.label,
            with_logs=with_logs,
            model_override=model_override,
            reference_photos_override=refs_for_this_pass,
        )
        pass_results.append(result)
        prev_output_path = Path(result.output_path)

    pipeline_result = ImagePipelineResult(
        slot_id=slot.id,
        template_id=template_id,
        run_id=run_id,
        output_dir=str(out_dir),
        passes=pass_results,
    )

    # Add a marker file pointing at the final image — convenience for downstream
    final_link = out_dir / "final.txt"
    final_link.write_text(Path(pipeline_result.final_output_path).name)

    return pipeline_result


# ─── CLI: single-pass mode (for prompt iteration without a template) ─────────


@click.command(help="Run a single image-to-image pass via fal.")
@click.option("--input", "input_image_path", required=True, type=click.Path(exists=True))
@click.option("--prompt", required=True, type=str)
@click.option("--model", "model_id", default="fal-ai/nano-banana/edit", show_default=True)
@click.option("--label", default="adhoc", show_default=True, help="Used in the output filename.")
@click.option("--output-dir", default=None, help="Defaults to outputs/adhoc-<timestamp>.")
@click.option("--aspect-ratio", default="16:9", show_default=True)
@click.option("--output-format", default="png", show_default=True, type=click.Choice(["png", "jpeg", "webp"]))
@click.option("--with-logs/--no-logs", default=True)
def _cli(
    input_image_path: str,
    prompt: str,
    model_id: str,
    label: str,
    output_dir: str | None,
    aspect_ratio: str,
    output_format: str,
    with_logs: bool,
) -> None:
    """Single-pass CLI — useful for iterating prompts without authoring a template."""
    out_dir = output_dir or f"outputs/adhoc-{_timestamp()}"
    pass_spec = ImagePass(
        label=label,
        source="input_photo",
        model=model_id,
        prompt=prompt,
        parameters={"aspect_ratio": aspect_ratio, "output_format": output_format},
    )
    result = run_image_pass(
        input_image_path=input_image_path,
        pass_spec=pass_spec,
        output_dir=out_dir,
        pass_index=1,
        pass_label=label,
        with_logs=with_logs,
    )
    click.echo("\nDONE")
    click.echo(f"  output: {result.output_path}")
    click.echo(f"  url:    {result.output_url}")
    click.echo(f"  time:   {result.duration_sec}s")


if __name__ == "__main__":  # pragma: no cover
    _cli()
