"""Topaz Starlight video upscale + generative detail reconstruction.

Why this matters for our pipeline:
  DepthFlow produces architecturally-locked motion but at the edges of the
  frame (where the camera has moved into territory not in the source photo)
  pixels stretch. Center-crop overscan hides most of this, but the remaining
  visible artifacts still look "Photoshop-stretched."

  Topaz Starlight is a diffusion model specifically tuned for upscaling AI-
  generated video — it reconstructs missing detail and smooths over the
  artifacts that classical upscalers would just sharpen. Unlike Lucy Restyle
  (which restyles the whole frame and tends to look "AI'd"), Starlight is a
  restoration model — it tries to preserve the original look while filling in
  detail that's plausibly there.

  Endpoint: fal-ai/topaz/upscale/video
  Variants accessible via the `model` field — Starlight Precise 2.5 is the
  newest (March 2026, 6B params, designed for AI-gen video → 4K).

This module exposes a small surface around it and a few preset configurations
tuned for our depthflow output.
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


TOPAZ_VIDEO_MODEL = "fal-ai/topaz/upscale/video"

# All available models from fal's Topaz endpoint. Starlight* are the diffusion
# generative variants; the rest are classical (Proteus, Artemis, Nyx, Gaia).
KNOWN_TOPAZ_MODELS: list[str] = [
    # Classical upscalers (no generative repair)
    "Proteus",
    "Artemis HQ",
    "Artemis MQ",
    "Artemis LQ",
    "Nyx",
    "Nyx Fast",
    "Nyx XL",
    "Nyx HF",
    "Gaia HQ",
    "Gaia CG",
    "Gaia 2",
    # Starlight — diffusion-based generative restoration
    "Starlight Precise 1",
    "Starlight Precise 2",
    "Starlight Precise 2.5",
    "Starlight HQ",
    "Starlight Mini",
    "Starlight Sharp",
    "Starlight Fast 1",
    "Starlight Fast 2",
]


# Preset configurations tuned for our use case. The bias is toward "preserve
# the original look, just clean up the depthflow stretching."
#
# Notable knobs (per fal docs):
#   recover_detail (0..1) — higher preserves more original detail (we want high)
#   noise (0..1)          — denoise strength (our source isn't noisy → 0)
#   compression (0..1)    — artifact removal (no compression issues → 0)
#   halo (0..1)           — halo reduction (small amount fine)
#   grain (0..1)          — adds film grain (cinematic touch — taste)
#   upscale_factor (1..4) — 2x = 1080p → 4K
PRESETS: dict[str, dict[str, Any]] = {
    "starlight_subtle": {
        "model": "Starlight Precise 2.5",
        "upscale_factor": 1.5,
        "recover_detail": 0.85,
        "noise": 0.0,
        "compression": 0.0,
        "grain": 0.0,
        "H264_output": True,
        "notes": (
            "Most conservative. 1.5x upscale, max original-detail preservation, "
            "no grain added. Use when you want a cleanup pass with minimal "
            "stylistic change."
        ),
    },
    "starlight_4k": {
        "model": "Starlight Precise 2.5",
        "upscale_factor": 2.0,
        "recover_detail": 0.90,
        "noise": 0.0,
        "compression": 0.0,
        "grain": 0.05,
        "H264_output": True,
        "notes": (
            "1080p → 4K via diffusion reconstruction. Max recover_detail to "
            "minimize Starlight's tendency to invent. Tiny grain for film feel. "
            "Best for hero shots."
        ),
    },
    "starlight_film": {
        "model": "Starlight Precise 2.5",
        "upscale_factor": 2.0,
        "recover_detail": 0.85,
        "noise": 0.0,
        "compression": 0.0,
        "grain": 0.25,
        "H264_output": True,
        "notes": (
            "Same as 4K but with more pronounced film grain for editorial "
            "cinematic feel. Try this if the unmodified output looks too "
            "clean / digital."
        ),
    },
    "starlight_fast": {
        "model": "Starlight Fast 2",
        "upscale_factor": 2.0,
        "recover_detail": 0.85,
        "noise": 0.0,
        "compression": 0.0,
        "grain": 0.0,
        "H264_output": True,
        "notes": (
            "Cheaper / faster Starlight variant for iteration. Use during "
            "tuning; switch to Precise 2.5 for the final."
        ),
    },
    "starlight_sharp": {
        "model": "Starlight Sharp",
        "upscale_factor": 2.0,
        "recover_detail": 0.85,
        "noise": 0.0,
        "compression": 0.0,
        "grain": 0.05,
        "H264_output": True,
        "notes": (
            "Sharper variant — accentuates fine detail. May exaggerate "
            "depthflow's edge stretching; use with overscan. Good for shots "
            "with strong materials (stone, wood grain) you want to feature."
        ),
    },
}


class StarlightError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _download(url: str, dest: Path, timeout: float = 300.0) -> None:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def run_starlight_upscale(
    input_video_path: str | Path,
    output_dir: str | Path,
    *,
    preset: str | None = "starlight_subtle",
    model: str | None = None,
    upscale_factor: float | None = None,
    recover_detail: float | None = None,
    noise: float | None = None,
    compression: float | None = None,
    halo: float | None = None,
    grain: float | None = None,
    target_fps: int | None = None,
    h264_output: bool | None = None,
    slot_id: str = "ad-hoc",
    template_id: str = "ad-hoc",
    run_id: str | None = None,
    with_logs: bool = False,
) -> dict[str, Any]:
    """Run a video through Topaz Starlight (or other Topaz model) upscale.

    Two ways to specify settings:
      - Pick a preset (default 'starlight_subtle') — sets all sane values.
      - Pass individual parameters to override the preset (or to specify
        from scratch by setting preset=None).

    Args:
        input_video_path: local mp4 to upscale.
        output_dir: where to write the upscaled mp4.
        preset: name in PRESETS, or None to specify everything by hand.
        model / upscale_factor / etc.: per-call overrides. Any value not
            None overrides the corresponding preset value.
        slot_id / template_id / run_id: stamped into metadata.
        with_logs: stream fal logs to stdout.

    Returns:
        Dict with output_path, output_url, params used, wall_time_sec.
    """
    in_path = Path(input_video_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input video not found: {in_path}")

    # Compose effective settings: start from preset, layer overrides on top.
    settings: dict[str, Any] = {}
    if preset is not None:
        if preset not in PRESETS:
            raise StarlightError(
                f"Unknown preset {preset!r}. Available: {sorted(PRESETS.keys())}"
            )
        settings.update(PRESETS[preset])
        settings.pop("notes", None)
    overrides = {
        "model": model,
        "upscale_factor": upscale_factor,
        "recover_detail": recover_detail,
        "noise": noise,
        "compression": compression,
        "halo": halo,
        "grain": grain,
        "target_fps": target_fps,
        "H264_output": h264_output,
    }
    for k, v in overrides.items():
        if v is not None:
            settings[k] = v

    if "model" not in settings:
        raise StarlightError(
            "No model specified. Pass preset= or model= explicitly."
        )
    if settings["model"] not in KNOWN_TOPAZ_MODELS:
        raise StarlightError(
            f"Unknown Topaz model {settings['model']!r}. Known: "
            f"{', '.join(KNOWN_TOPAZ_MODELS)}"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if run_id is None:
        run_id = f"{_timestamp()}_{slot_id}_starlight_{uuid.uuid4().hex[:6]}"

    out_path = out_dir / f"{slot_id}_starlight.mp4"

    print(f"  [starlight] uploading {in_path.name} to fal...")
    video_url = fal_client.upload_local_file(in_path)

    arguments: dict[str, Any] = {"video_url": video_url, **settings}

    print(
        f"  [starlight] model={settings['model']} "
        f"upscale={settings.get('upscale_factor', 2.0)}x "
        f"recover_detail={settings.get('recover_detail', 'default')}"
    )

    started = time.time()
    response = fal_client.subscribe(TOPAZ_VIDEO_MODEL, arguments, with_logs=with_logs)
    duration_wall = time.time() - started

    # Output: {"video": {"url": "..."}}
    if isinstance(response.get("video"), dict) and "url" in response["video"]:
        output_url = response["video"]["url"]
    elif isinstance(response.get("video"), str):
        output_url = response["video"]
    else:
        raise StarlightError(
            f"Could not extract video URL from response. "
            f"Top-level keys: {list(response.keys())}"
        )

    print(f"  [starlight] downloading upscaled mp4 → {out_path}")
    _download(output_url, out_path)

    metadata = {
        "kind": "starlight_upscale",
        "endpoint": TOPAZ_VIDEO_MODEL,
        "run_id": run_id,
        "slot_id": slot_id,
        "template_id": template_id,
        "preset": preset,
        "settings": settings,
        "input_video_path": str(in_path),
        "output_path": str(out_path),
        "output_url": output_url,
        "wall_time_sec": round(duration_wall, 2),
    }

    meta_path = out_dir / "starlight_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, default=str))

    print(f"  [starlight] done in {duration_wall:.1f}s")
    return metadata


def list_presets() -> list[tuple[str, str]]:
    """Return [(preset_name, notes), ...] for CLI listing."""
    return [(name, p["notes"]) for name, p in PRESETS.items()]
