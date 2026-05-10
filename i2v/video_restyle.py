"""Video-to-video restyle pass via fal's decart/lucy-restyle.

The "Asteria pattern" applied to our pipeline:
  1. DepthFlow produces the architectural base (geometry locked, real pixels).
  2. Lucy Restyle adds cinematic atmosphere on top — color grade, grain, lens
     character, lighting mood. Generative, but constrained to a video that
     already has correct architecture.

Lucy Restyle is the real v2v option on fal. Kling and Seedance are i2v only.

Caveats worth knowing:
  - No strength/denoise parameter. Lucy decides how much to restyle based on
    the prompt. The way to keep it conservative is prompt discipline:
    explicitly ask to PRESERVE architecture, materials, composition, etc.
  - 720p only output. DepthFlow renders 1080p; Lucy downsamples. If we want
    final 1080p we'd add a Topaz upscale pass after.
  - Tuned for visible restyle (the canonical example is "make it psychedelic").
    Subtle cinematic looks require carefully worded prompts and may take 2–4
    iterations to dial in.

Conservative prompt patterns that tend to work (use as starting points):

  "Apply subtle cinematic film grain and a warm color grade with slightly
   desaturated highlights. Preserve all architecture, materials, fixtures,
   and composition exactly. Do not change geometry, do not move objects, do
   not invent any feature."

  "Editorial cinematic look with controlled shadows and warm midtones.
   Photorealistic. Maintain exact composition and architecture from the
   source. Add subtle 35mm film grain. Treat as a color/tone grade only."

  "Apply a Roger Deakins style cinematic grade — neutral whites, deep blacks,
   subtle warm tint in shadows, controlled highlight rolloff. Architecture
   and composition unchanged."

The library exposes a single function. Use it directly or call via the
`scripts/run_v2v.py` CLI for ad-hoc experimentation.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from i2v import fal_client


LUCY_RESTYLE_MODEL = "decart/lucy-restyle"

# Prompt patterns we've found work for "preserve architecture, add cinematic
# atmosphere only." Surface these via the CLI as `--style cinematic_warm`
# etc. Tuned conservatively — bias is toward "barely changed" not "obviously
# stylized."
PROMPT_PRESETS: dict[str, str] = {
    "cinematic_warm": (
        "Apply subtle cinematic film grain and a warm editorial color grade. "
        "Slightly lifted midtones, controlled highlight rolloff, deep but not "
        "crushed blacks. Warm tint in shadows. Preserve all architecture, "
        "materials, fixtures, and composition exactly. Do not change geometry, "
        "do not move or modify any object, do not invent any feature. "
        "Photorealistic."
    ),
    "cinematic_neutral": (
        "Apply subtle 35mm film grain and a neutral cinematic grade. "
        "Photorealistic. Maintain exact composition and architecture from the "
        "source. Subtle desaturation. Treat as a color/tone grade only — do "
        "not change geometry or modify any object."
    ),
    "deakins_style": (
        "Apply a Roger Deakins style cinematic grade — neutral whites, deep "
        "blacks, subtle warm tint in shadows, controlled highlight rolloff, "
        "subtle natural film grain. Architecture, composition, and all "
        "materials unchanged. Photorealistic. No geometry changes."
    ),
    "evening_mood": (
        "Shift to a warm late-afternoon golden-hour mood. Subtle warm color "
        "grade, gentle film grain, slightly hazy atmosphere. Preserve all "
        "architecture and composition exactly — do not change time of day "
        "lighting direction in a way that moves shadows. Photorealistic."
    ),
    "subtle_haze": (
        "Add a subtle atmospheric haze and very mild cinematic grain. Slight "
        "warm shift in highlights. Preserve architecture, materials, and "
        "composition exactly. Photorealistic. Do not invent or change any "
        "object."
    ),
}


class V2VError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _download(url: str, dest: Path, timeout: float = 180.0) -> None:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def run_v2v_restyle(
    input_video_path: str | Path,
    output_dir: str | Path,
    *,
    prompt: str,
    enhance_prompt: bool = False,
    seed: int | None = None,
    slot_id: str = "ad-hoc",
    template_id: str = "ad-hoc",
    run_id: str | None = None,
    with_logs: bool = False,
) -> dict[str, Any]:
    """Run a video-to-video restyle pass via Lucy Restyle.

    Args:
        input_video_path: local mp4 to restyle. Typically the output of a
            DepthFlow run.
        output_dir: where to write the restyled mp4 + metadata.
        prompt: the style description. See PROMPT_PRESETS for conservative
            starting points.
        enhance_prompt: if True, fal expands the prompt before generation.
            DEFAULT False — we want our prompt used verbatim because we've
            tuned it for conservative restyle.
        seed: optional integer for reproducibility.
        slot_id / template_id / run_id: stamped into metadata for audit.
        with_logs: stream fal logs to stdout.

    Returns:
        Metadata dict with output_path, output_url, duration, prompt used.
    """
    in_path = Path(input_video_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input video not found: {in_path}")
    if not in_path.suffix.lower() in {".mp4", ".mov", ".webm"}:
        raise V2VError(
            f"Lucy Restyle expects an mp4/mov/webm input. Got: {in_path.suffix}"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if run_id is None:
        run_id = f"{_timestamp()}_{slot_id}_v2v_{uuid.uuid4().hex[:6]}"

    out_path = out_dir / f"{slot_id}_restyled.mp4"

    # 1. Upload the input video to fal so Lucy can fetch it.
    print(f"  [v2v] uploading input video {in_path.name} to fal...")
    video_url = fal_client.upload_local_file(in_path)

    # 2. Build arguments.
    arguments: dict[str, Any] = {
        "video_url": video_url,
        "prompt": prompt,
        "resolution": "720p",
        "enhance_prompt": enhance_prompt,
    }
    if seed is not None:
        arguments["seed"] = seed

    print(
        f"  [v2v] running model={LUCY_RESTYLE_MODEL} "
        f"prompt={prompt[:80]!r}..."
    )

    # 3. Submit synchronously.
    started = time.time()
    response = fal_client.subscribe(LUCY_RESTYLE_MODEL, arguments, with_logs=with_logs)
    duration_wall = time.time() - started

    # 4. Pull video URL from response and download.
    if isinstance(response.get("video"), dict) and "url" in response["video"]:
        output_url = response["video"]["url"]
    elif isinstance(response.get("video"), str):
        # Some fal endpoints return the URL as a bare string
        output_url = response["video"]
    else:
        raise V2VError(
            f"Could not extract video URL from Lucy Restyle response. "
            f"Top-level keys: {list(response.keys())}"
        )

    print(f"  [v2v] downloading restyled mp4 → {out_path}")
    _download(output_url, out_path)

    metadata = {
        "kind": "v2v_restyle",
        "model": LUCY_RESTYLE_MODEL,
        "run_id": run_id,
        "slot_id": slot_id,
        "template_id": template_id,
        "input_video_path": str(in_path),
        "output_path": str(out_path),
        "output_url": output_url,
        "prompt": prompt,
        "enhance_prompt": enhance_prompt,
        "seed": seed,
        "wall_time_sec": round(duration_wall, 2),
        "resolution": "720p",
    }

    meta_path = out_dir / "v2v_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, default=str))

    print(f"  [v2v] done in {duration_wall:.1f}s")
    return metadata


def list_prompt_presets() -> list[tuple[str, str]]:
    """Return [(preset_name, full_prompt), ...] for CLI listing."""
    return list(PROMPT_PRESETS.items())
