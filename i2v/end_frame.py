"""End-frame synthesis for slots that need a generative camera move.

When a slot's plan has a non-trivial end_frame_strategy, this module produces
the end keyframe that gets fed to a fal i2v model (e.g. Kling 2.6 Pro
`tail_image_url` or Seedance 2.0 `end_image_url`).

Three strategies, mirroring the ones the classifier emits in
SlotAssignment.end_frame_strategy:

  • real_reference     — the plan picked an actual photo that closely
                         matches the start photo (same room, different angle).
                         We run that photo through the slot's image_pipeline
                         (with the same multi-reference set) so its style
                         matches the start frame's enhancement, then return
                         the resulting path.

  • multi_ref_inpaint  — no real alternate available. We render a single
                         frame with DepthFlow at peak intensity (real pixels
                         where possible, stretched at the edges), generate a
                         border mask covering the stretched region, and run
                         FLUX inpaint to fill ONLY that masked region with a
                         prompt that names the architectural style. Inner
                         ~85% is real, outer ~15% is generative-but-bounded.

  • depthflow_only     — no end frame at all. The video pass uses DepthFlow
                         with no tail image. (Returned as None.)

Routing constraint: every model call goes through fal. Inpaint endpoint is
fal-ai/flux-pro/v1/inpainting (override at module top if the route name
shifts).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

from i2v import fal_client
from i2v.depth_video import DEPTHFLOW_PRESETS, _depthflow_available
from i2v.image_pass import run_image_pipeline
from i2v.types import Slot


# FLUX inpaint endpoint on fal. The mask-based inpaint product is called
# "Fill" (fal-ai/flux-pro/v1/fill), not "inpainting" — the latter route
# returns 404. Schema is image_url + mask_url + prompt; same fields we
# already pass.
INPAINT_ENDPOINT = "fal-ai/flux-pro/v1/fill"

# Default border for the inpaint mask. 0.15 = the outer 15% of the frame is
# considered "stretched / generative"; the inner 85% is preserved exactly.
DEFAULT_INPAINT_BORDER_PCT = 0.15

# Intensity for the depthflow extreme used as the end frame. 1.75 is a
# middle-ground value: visible displacement without the heavy edge-stretching
# that intensity 2.5+ produces. Override per-run via --end-frame-intensity.
DEFAULT_END_FRAME_INTENSITY = 1.75

# Render time for the depthflow extreme. Longer = more reliable extraction
# of the peak-displacement final frame via ffmpeg `-sseof`. 3 seconds is
# overkill for the clip we'll throw away (we only keep the last frame),
# but the cost is negligible (~5-8s) and we get a more decisive peak.
DEFAULT_END_FRAME_RENDER_SEC = 3


class EndFrameError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


# ─── Mask generation ────────────────────────────────────────────────────────


def generate_border_mask(
    width: int,
    height: int,
    output_path: str | Path,
    *,
    border_pct: float = DEFAULT_INPAINT_BORDER_PCT,
    feather_px: int = 8,
) -> Path:
    """Write a white-border / black-center PNG mask to output_path.

    For FLUX inpaint convention: WHITE pixels are where the model generates,
    BLACK pixels are preserved exactly. The outer border_pct of the frame
    becomes white; the inner region stays black. The transition is feathered
    by `feather_px` pixels so the inpaint blends seamlessly.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Start fully white (modify everywhere)
    mask = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(mask)

    # Carve out the inner rectangle (preserve = black)
    inner_x = int(width * border_pct)
    inner_y = int(height * border_pct)
    inner_w = width - 2 * inner_x
    inner_h = height - 2 * inner_y
    draw.rectangle(
        [inner_x, inner_y, inner_x + inner_w, inner_y + inner_h],
        fill=0,
    )

    # Feather the boundary so inpaint blends. Pillow doesn't have a built-in
    # gaussian blur on a binary mask, but a quick approximation: dilate
    # slightly inward by drawing a slightly-larger black rectangle with
    # decreasing alpha. Simpler: use the Gaussian filter in PIL.
    if feather_px > 0:
        from PIL import ImageFilter

        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_px))

    mask.save(out_path, format="PNG")
    return out_path


# ─── DepthFlow single-frame extreme position ─────────────────────────────────


def render_depthflow_extreme_frame(
    input_image_path: str | Path,
    output_path: str | Path,
    *,
    motion: str = "horizontal",
    intensity: float = DEFAULT_END_FRAME_INTENSITY,
    isometric: float = 0.85,
    width: int = 1920,
    height: int = 1080,
    render_sec: int = DEFAULT_END_FRAME_RENDER_SEC,
) -> Path:
    """Render a single frame at the END position of a depthflow camera move.

    Implementation: render a short clip at the desired intensity, then
    extract the last frame with ffmpeg. (DepthFlow's CLI doesn't have a
    'render single frame at time T' mode that we can rely on; rendering a
    few seconds of clip and grabbing the last frame is robust across
    versions.)
    """
    in_path = Path(input_image_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input image not found: {in_path}")
    if not _depthflow_available():
        raise EndFrameError(
            "depthflow CLI not on PATH. Install with `pipx install depthflow` "
            "or grab the portable executable from "
            "https://github.com/BrokenSource/DepthFlow/releases"
        )

    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_clip = out_path.parent / f".{out_path.stem}_depthflow_clip.mp4"

    # Render a 1-second clip with peak intensity. We use `--no-loop` so the
    # camera plays the full motion arc once across the duration; the last
    # frame is at peak displacement (because phase=0 by default — start at
    # center, end at extreme).
    args = [
        "depthflow",
        "input",
        "-i",
        str(in_path),
        motion,
        "--intensity",
        str(intensity),
        "--isometric",
        str(isometric),
        "--phase",
        "0.0",
        "--no-loop",
        "main",
        "--width",
        str(width),
        "--height",
        str(height),
        "--fps",
        "24",
        "--time",
        str(render_sec),
        "--output",
        str(tmp_clip),
    ]
    print(f"  [end-frame] rendering depthflow extreme: $ {' '.join(args)}")
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        raise EndFrameError(
            f"depthflow render failed: {e.stderr}"
        ) from e

    if not tmp_clip.exists():
        raise EndFrameError(f"depthflow produced no output at {tmp_clip}")

    # Extract the last frame as PNG.
    print(f"  [end-frame] extracting last frame from clip → {out_path.name}")
    if not shutil.which("ffmpeg"):
        raise EndFrameError(
            "ffmpeg not found on PATH. `brew install ffmpeg`."
        )
    ff_args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-sseof",
        "-0.05",  # seek 0.05s from end of file
        "-i",
        str(tmp_clip),
        "-update",
        "1",
        "-frames:v",
        "1",
        str(out_path),
    ]
    try:
        subprocess.run(ff_args, check=True, capture_output=True, text=True, timeout=60)
    except subprocess.CalledProcessError as e:
        raise EndFrameError(f"ffmpeg frame extract failed: {e.stderr}") from e

    if not out_path.exists():
        raise EndFrameError(f"ffmpeg produced no output at {out_path}")

    # Clean up intermediate clip — we only needed the last frame.
    try:
        tmp_clip.unlink()
    except OSError:
        pass

    return out_path


# ─── FLUX inpaint call ───────────────────────────────────────────────────────


def inpaint_with_mask(
    image_path: str | Path,
    mask_path: str | Path,
    output_path: str | Path,
    *,
    prompt: str,
    additional_reference_paths: list[str | Path] | None = None,
    with_logs: bool = False,
) -> Path:
    """Run a masked inpaint via fal. Inner mask region is preserved; the white
    border is generated.

    additional_reference_paths is currently NOT used by FLUX inpaint (the
    endpoint accepts a single image). It's recorded in metadata so we can
    track what *would* have been references; future inpaint endpoints that
    support multi-image could use them.
    """
    img_path = Path(image_path).resolve()
    msk_path = Path(mask_path).resolve()
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"  [end-frame] uploading inpaint inputs to fal...")
    image_url = fal_client.upload_local_file(img_path)
    mask_url = fal_client.upload_local_file(msk_path)

    arguments: dict[str, Any] = {
        "image_url": image_url,
        "mask_url": mask_url,
        "prompt": prompt,
        "num_images": 1,
        "output_format": "png",
        "safety_tolerance": "5",  # max permissive; we're inpainting interior shots
    }

    print(
        f"  [end-frame] running FLUX inpaint with prompt={prompt[:80]!r}..."
    )
    response = fal_client.subscribe(
        INPAINT_ENDPOINT, arguments, with_logs=with_logs
    )

    # FLUX inpaint output: {"images": [{"url": "..."}]}
    if isinstance(response.get("images"), list) and response["images"]:
        first = response["images"][0]
        if isinstance(first, dict) and "url" in first:
            output_url = first["url"]
        else:
            raise EndFrameError(
                f"Unexpected FLUX inpaint response shape: {response}"
            )
    else:
        raise EndFrameError(
            f"FLUX inpaint response had no 'images' array: {response}"
        )

    print(f"  [end-frame] downloading inpaint result → {out_path}")
    _download(output_url, out_path)
    return out_path


# ─── Strategy orchestrators ──────────────────────────────────────────────────


def _build_inpaint_prompt(
    slot_label: str,
    primary_classification_notes: str,
) -> str:
    """A prompt that asks the inpaint to extend the existing scene naturally.

    Bias is hard toward 'preserve and extend' — we're trying to fill in the
    edges of a depthflow-stretched frame, not invent new content.
    """
    base = (
        f"Photorealistic continuation of a {slot_label.lower()}. "
        f"Extend the visible architecture, materials, lighting, and color "
        f"palette of the surrounding image naturally into the masked border "
        f"region. Do not add windows, doors, or fixtures that aren't already "
        f"implied by the surrounding scene. Match the white balance, exposure, "
        f"and shadow direction of the inner image exactly. Editorial cinematic "
        f"style. Preserve the architectural language."
    )
    if primary_classification_notes:
        base += f" Source notes: {primary_classification_notes}"
    return base


def synthesize_end_frame_multi_ref_inpaint(
    start_frame_path: str | Path,
    output_dir: str | Path,
    *,
    motion: str = "horizontal",
    intensity: float = DEFAULT_END_FRAME_INTENSITY,
    border_pct: float = DEFAULT_INPAINT_BORDER_PCT,
    width: int = 1920,
    height: int = 1080,
    slot_label: str = "interior view",
    primary_notes: str = "",
    additional_reference_paths: list[str | Path] | None = None,
    with_logs: bool = False,
    render_sec: int = DEFAULT_END_FRAME_RENDER_SEC,
) -> dict[str, Any]:
    """End-frame synthesis: render the depthflow extreme position and use it
    directly as the end frame. No FLUX inpaint step.

    Why we removed the inpaint:
      FLUX Pro Fill kept misinterpreting the rectangular border mask as a
      website-mockup frame and generating UI chrome instead of extending the
      kitchen scene. The fix is structural — Kling/Seedance's interpolation
      between a clean start frame and a slightly-stretched end frame produces
      motion where the stretching only manifests in the very last frame of
      the video. That's not noticeable to a viewer.

      The inpaint helper functions (inpaint_with_mask, generate_border_mask)
      remain in this module so a future multi-image-conditioned inpaint
      can swap in cleanly when one becomes available. The strategy name
      'multi_ref_inpaint' is preserved in the schema for backwards
      compatibility but the behavior is now 'depthflow_extreme_endframe'.

    Steps:
      1. Render a depthflow extreme-position frame at high intensity
         (default 2.5 — produces clearly cinematic displacement).
      2. Use that frame directly as the end frame for the video model.

    Returns metadata describing the render. The 'final_end_frame_path' is
    the depthflow extreme image; downstream code can use it as Kling's
    end_image_url or Seedance's end_image_url.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    depth_frame_path = out_dir / "end_frame_depthflow_extreme.png"

    started = time.time()
    render_depthflow_extreme_frame(
        input_image_path=start_frame_path,
        output_path=depth_frame_path,
        motion=motion,
        intensity=intensity,
        width=width,
        height=height,
        render_sec=render_sec,
    )
    duration_wall = time.time() - started

    metadata = {
        "kind": "end_frame_depthflow_extreme",  # was 'end_frame_multi_ref_inpaint'
        "start_frame_path": str(Path(start_frame_path).resolve()),
        "depthflow_extreme_frame_path": str(depth_frame_path),
        "final_end_frame_path": str(depth_frame_path),  # same file — no inpaint
        "motion": motion,
        "intensity": intensity,
        "render_sec": render_sec,
        "width": width,
        "height": height,
        "additional_reference_paths_recorded": [
            str(p) for p in (additional_reference_paths or [])
        ],
        "wall_time_sec": round(duration_wall, 2),
        "inpaint_used": False,
        "inpaint_skip_reason": (
            "FLUX Pro Fill misinterprets rectangular border masks as "
            "website-mockup frames. Removed; Kling/Seedance interpolation "
            "smooths over edge stretching across the clip duration."
        ),
    }
    (out_dir / "end_frame_metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str)
    )

    return metadata


def synthesize_end_frame_real_reference(
    end_frame_photo_path: str | Path,
    slot: Slot,
    output_dir: str | Path,
    *,
    additional_reference_paths: list[str | Path] | None = None,
    with_logs: bool = False,
) -> dict[str, Any]:
    """Style-match a real alternate-angle photo to the start frame.

    Runs the slot's image_pipeline on the alternate photo with the same
    multi-reference set used for the primary, so styles are consistent.
    """
    refs_str = [str(p) for p in (additional_reference_paths or [])]
    pipeline_result = run_image_pipeline(
        slot=slot,
        input_image_path=end_frame_photo_path,
        output_root=str(output_dir),
        template_id="end-frame-real-reference",
        with_logs=with_logs,
        reference_photos_override=refs_str,
    )
    return {
        "kind": "end_frame_real_reference",
        "source_photo_path": str(Path(end_frame_photo_path).resolve()),
        "final_end_frame_path": pipeline_result.final_output_path,
        "image_pipeline_run_id": pipeline_result.run_id,
        "image_pipeline_output_dir": pipeline_result.output_dir,
        "passes": [p.model_dump() for p in pipeline_result.passes],
    }
