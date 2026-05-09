"""Video-pass module — image-to-video via fal.

One public entry point:
  - `run_video_pass(input_image_path, video_pass_spec, output_dir, ...)` — runs
    one image-to-video generation. Pure library function; reused by CLIs and by
    the eventual full template runner.

Auditable: writes a `video_metadata.json` next to the mp4 with the exact model,
prompt, duration, params, wall-clock time, and any end-frame path used.

CLI mode (no template needed) — useful for prompt iteration:

    python -m i2v.video_pass \\
        --input outputs/.../pass_01_editorial_enhance.png \\
        --prompt "Wide interior shot with slow trucking..." \\
        --model fal-ai/kling-video/v2.6/pro/image-to-video \\
        --duration 6 \\
        --output-dir outputs/quick-video
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import httpx

from i2v import fal_client
from i2v.types import VideoPass, VideoPassResult
from i2v.video_models import VideoModelAdapter, resolve_video_model


# ─── Internal helpers ────────────────────────────────────────────────────────


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _ensure_output_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(url: str, dest: Path, timeout: float = 180.0) -> None:
    """Stream a URL to disk. Video files can be large; avoid loading in memory."""
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def _write_video_metadata(output_dir: Path, payload: dict[str, Any]) -> None:
    meta_path = output_dir / "video_metadata.json"
    meta_path.write_text(json.dumps(payload, indent=2, default=str))


# ─── run_video_pass — single video pass ──────────────────────────────────────


def run_video_pass(
    input_image_path: str | Path,
    video_pass_spec: VideoPass,
    output_dir: str | Path,
    *,
    slot_id: str = "ad-hoc",
    template_id: str = "ad-hoc",
    run_id: str | None = None,
    end_frame_image_path: str | Path | None = None,
    with_logs: bool = False,
    model_override: str | None = None,
    duration_override: int | None = None,
) -> VideoPassResult:
    """Run one image-to-video generation.

    Args:
        input_image_path: local path to the START keyframe — typically the
            final output of the slot's image pipeline.
        video_pass_spec: the VideoPass spec from the template.
        output_dir: directory for the mp4 + video_metadata.json.
        slot_id / template_id: stamped into metadata for audit.
        run_id: stable id for this run; auto-generated if not provided.
        end_frame_image_path: optional second keyframe (only used if the model
            supports end-frame and the template requested one).
        with_logs: stream fal logs to stdout while the model runs.
        model_override: ignore video_pass_spec.model and use this id instead.
        duration_override: ignore video_pass_spec.duration_sec; clamp to model's
            allowed range.

    Returns:
        VideoPassResult with the local mp4 path, the fal CDN URL, and timing.
    """
    in_path = Path(input_image_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input image not found: {in_path}")

    out_dir = _ensure_output_dir(output_dir)
    run_id = run_id or f"{_timestamp()}_{slot_id}_{uuid.uuid4().hex[:6]}"

    model_id = model_override or video_pass_spec.model
    adapter: VideoModelAdapter = resolve_video_model(model_id)

    requested_duration = duration_override or video_pass_spec.duration_sec
    duration_sec = adapter.clamp_duration(requested_duration)
    if duration_sec != requested_duration:
        print(
            f"  [video] duration {requested_duration}s clamped to {duration_sec}s "
            f"(model {model_id} allows {adapter.allowed_durations or f'{adapter.min_duration}-{adapter.max_duration}'})"
        )

    # Upload start frame
    print(f"  [video] uploading start frame {in_path.name} to fal...")
    image_url = fal_client.upload_local_file(in_path)

    # Optional end frame
    end_frame_url: str | None = None
    end_frame_resolved: Path | None = None
    if end_frame_image_path is not None:
        end_frame_resolved = Path(end_frame_image_path).resolve()
        if not adapter.supports_end_frame:
            print(
                f"  [video] WARN: model {model_id} does not support end frames; "
                f"ignoring end_frame_image_path."
            )
        else:
            if not end_frame_resolved.exists():
                raise FileNotFoundError(f"End frame not found: {end_frame_resolved}")
            print(f"  [video] uploading end frame {end_frame_resolved.name} to fal...")
            end_frame_url = fal_client.upload_local_file(end_frame_resolved)

    # Merge default extras (model-level, e.g. {generate_audio: false}) with template extras.
    # Template extras win on conflict.
    extras = {**adapter.default_extra_args, **dict(video_pass_spec.extra_args)}

    arguments = adapter.build_arguments(
        image_url=image_url,
        prompt=video_pass_spec.prompt,
        duration_sec=duration_sec,
        end_frame_url=end_frame_url,
        extras=extras,
    )

    print(
        f"  [video] running model={model_id} duration={duration_sec}s "
        f"prompt={video_pass_spec.prompt[:80]!r}..."
    )

    started = time.time()
    response = fal_client.subscribe(model_id, arguments, with_logs=with_logs)
    duration_wall = time.time() - started

    output_url = adapter.extract_output_url(response)
    out_filename = f"{slot_id}_clip.mp4"
    out_path = out_dir / out_filename
    print(f"  [video] downloading mp4 → {out_path}")
    _download(output_url, out_path)

    result = VideoPassResult(
        slot_id=slot_id,
        template_id=template_id,
        run_id=run_id,
        model=model_id,
        prompt=video_pass_spec.prompt,
        duration_sec=duration_sec,
        input_image_path=str(in_path),
        end_frame_image_path=str(end_frame_resolved) if end_frame_resolved else None,
        output_path=str(out_path),
        output_url=output_url,
        duration_wall_sec=round(duration_wall, 2),
        extra_args=extras,
    )

    _write_video_metadata(out_dir, result.model_dump())
    print(f"  [video] done in {duration_wall:.1f}s")
    return result


# ─── CLI ─────────────────────────────────────────────────────────────────────


@click.command(help="Run one image-to-video pass via fal.")
@click.option("--input", "input_image_path", required=True, type=click.Path(exists=True))
@click.option("--prompt", required=True, type=str)
@click.option(
    "--model",
    "model_id",
    default="fal-ai/kling-video/v2.6/pro/image-to-video",
    show_default=True,
)
@click.option("--duration", "duration_sec", default=6, show_default=True, type=int)
@click.option(
    "--end-frame",
    "end_frame_path",
    default=None,
    type=click.Path(exists=True),
    help="Optional end keyframe for arrival/departure shots; only used if model supports it.",
)
@click.option("--slot-id", default="adhoc", show_default=True)
@click.option("--output-dir", default=None, help="Defaults to outputs/adhoc-video-<timestamp>.")
@click.option("--with-logs/--no-logs", default=True)
def _cli(
    input_image_path: str,
    prompt: str,
    model_id: str,
    duration_sec: int,
    end_frame_path: str | None,
    slot_id: str,
    output_dir: str | None,
    with_logs: bool,
) -> None:
    """Single-pass video CLI — useful for iterating prompts without a template."""
    out_dir = output_dir or f"outputs/adhoc-video-{_timestamp()}"
    spec = VideoPass(
        model=model_id,
        prompt=prompt,
        duration_sec=duration_sec,
        start_frame_source="image_pipeline.last",
        end_frame_source=None,
        extra_args={},
    )
    result = run_video_pass(
        input_image_path=input_image_path,
        video_pass_spec=spec,
        output_dir=out_dir,
        slot_id=slot_id,
        template_id="adhoc",
        end_frame_image_path=end_frame_path,
        with_logs=with_logs,
    )
    click.echo("\nDONE")
    click.echo(f"  output: {result.output_path}")
    click.echo(f"  url:    {result.output_url}")
    click.echo(f"  time:   {result.duration_wall_sec}s")


if __name__ == "__main__":  # pragma: no cover
    _cli()
