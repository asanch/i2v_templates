"""DepthFlow backend — local 2.5D parallax video from a single image.

Why this exists:
  Generative video models (Kling, Seedance) freely invent off-frame geometry
  when the camera moves through a 2D photo. For real estate that's a feature
  killer — the buyer sees a hallucinated room. DepthFlow is the industry-
  standard alternative: estimate a depth map, project pixels into a 3D point
  cloud, move a virtual camera, inpaint only the slivers the new viewpoint
  reveals. The architecture comes from the photo itself, so it can't drift.

This is the right default `video_pass` for any slot that's "just a camera
move" — wide trucks, slow pushes, hero pull-backs. Reserve generative video
for slots that genuinely need invented motion (water flowing, light shifting
across a surface, curtain unfurling).

Install:
    pipx install depthflow
    # or grab a portable executable from
    #   https://github.com/BrokenSource/DepthFlow/releases
    # and put it on PATH

We deliberately do NOT make `depthflow` a pip dep of i2v-templates. It pulls
torch + GUI libraries (imgui-bundle, glfw, shaderflow) that we don't import
because we only call the CLI via subprocess. pipx isolates that install in
its own venv.

DepthFlow uses an OpenGL/GLSL shader for the warp; on a Mac that means
running on Metal via the system GL driver. CPU fallback exists but is slow.
For our purposes a 5–8s clip at 1920x1080 should render in <30s on Apple
Silicon, dramatically faster than fal.

This module wraps the DepthFlow CLI via subprocess. The Python API is also
available but its surface has churned across versions; subprocess is the
stable contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Map our internal preset names to DepthFlow's CLI subcommands / animation
# names. DepthFlow ships these built-in.
#
# Intensity calibration:
#   ~0.2  — barely visible drift (use as base layer for masked motion only)
#   ~0.6  — gently cinematic, "slow" tier; reads as a real camera move
#   ~1.0  — clearly cinematic, "medium" tier; pronounced depth separation
#   ~1.5+ — dramatic; risks geometry stretch in shallow-depth photos
#
# Isometric controls how much foreground/background separate during motion.
# Higher = stronger parallax illusion; ~0.7–0.85 is the sweet spot for
# real-estate interiors.
#
# Cycles controls direction:
#   1.0 — one full sine cycle (DepthFlow default): goes out, comes back to start
#         (this looks "rocking" — usually NOT what we want for cinematic shots)
#   0.5 — half cycle: starts at center, ends at peak. One-way motion.
#   0.25 — quarter cycle: starts at peak, ends at center. Reveal-style.
# We default every preset to 0.5 (one-way) since that's what reads as a real
# camera move. Override per-preset if you want oscillation.
#
# Phase shifts the starting position in the cycle. With cycles=0.5:
#   phase=0   — start at the center of the photo, move out
#   phase=0.5 — start at the offset position, move back to center (reveal)
DEPTHFLOW_PRESETS: dict[str, dict[str, Any]] = {
    "slow_truck": {
        "animation": "horizontal",
        "intensity": 0.6,
        "isometric": 0.75,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Cinematic horizontal lateral parallax, one-way. Slow but clearly moving.",
    },
    "slow_dolly_in": {
        "animation": "zoom",
        "intensity": 0.6,
        "isometric": 0.75,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Cinematic forward push, one-way. Visible motion, no geometry invention.",
    },
    "slow_dolly_out": {
        "animation": "zoom",
        "intensity": -0.6,
        "isometric": 0.75,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Cinematic pull-back / reveal, one-way. Negative intensity reverses zoom.",
    },
    "orbit_subtle": {
        "animation": "circle",
        "intensity": 0.5,
        "isometric": 0.7,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Gentle one-way orbit around a focal point. Good for hero exterior.",
    },
    "vertical_pan": {
        "animation": "vertical",
        "intensity": 0.5,
        "isometric": 0.7,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "One-way vertical parallax. Use when the photo has strong vertical lines.",
    },
    "medium_truck": {
        "animation": "horizontal",
        "intensity": 1.0,
        "isometric": 0.85,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Pronounced one-way truck. More motion than slow_truck for hero shots.",
    },
    "medium_dolly_in": {
        "animation": "zoom",
        "intensity": 1.0,
        "isometric": 0.85,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Pronounced one-way forward push. Use for hero shots that want clear motion.",
    },
    "static_with_light_drift": {
        # Just the depth-projected stillness with a small ambient drift.
        # Useful as the "rule" layer underneath a generative "exception" pass.
        "animation": "horizontal",
        "intensity": 0.2,
        "isometric": 0.5,
        "cycles": 0.5,
        "phase": 0.0,
        "notes": "Near-still one-way drift. Base for masked motion overlays.",
    },
}


class DepthFlowError(RuntimeError):
    pass


def _depthflow_available() -> bool:
    """Return True if the depthflow CLI is on PATH."""
    return shutil.which("depthflow") is not None


def _build_cli_args(
    input_image: Path,
    output_video: Path,
    preset: str,
    duration_sec: int,
    fps: int,
    width: int,
    height: int,
    extra_cli: dict[str, Any] | None,
    intensity_override: float | None = None,
    steady_override: float | None = None,
) -> list[str]:
    """Build the argv for the depthflow CLI invocation.

    The CLI shape (as of DepthFlow ~0.10):
        depthflow input -i <path> [animation] --intensity X --isometric Y \\
            main --width W --height H --fps F --time SEC --output PATH

    DepthFlow's CLI has evolved; we keep the call shape conservative and
    build it from the preset table. If the user has a newer version with
    different flags, the `extra_cli` dict can override any single flag.
    """
    if preset not in DEPTHFLOW_PRESETS:
        raise DepthFlowError(
            f"Unknown DepthFlow preset {preset!r}. "
            f"Available: {sorted(DEPTHFLOW_PRESETS.keys())}"
        )
    p = DEPTHFLOW_PRESETS[preset]

    # Animation flags (applied to the animation subcommand).
    #
    # DepthFlow 0.9.x CLI exposes:
    #   --intensity (0..4)   — global motion amplitude
    #   --isometric (0..1)   — flatness 0=perspective, 1=isometric
    #   --phase (0..1)       — where in the wave to start
    #   --steady (-1..2)     — depth value of "no displacement" anchor
    #   --no-loop / --loop   — boolean; with --no-loop the wave plays once
    #                          over the duration instead of looping at 4x
    #   --linear / --smooth  — triangle wave vs sine wave easing
    #   --reverse / --forward — direction
    #
    # IMPORTANT: --no-loop IS effectively one-way motion. The base animation
    # is a half-cycle (one direction); --loop replays it 4x (the docs' "4x
    # apparent frequency"), which is what creates the back-and-forth feel.
    # With --no-loop the camera moves monotonically across the duration —
    # the start frame and end frame are clearly different positions, no return.
    # Verified empirically; the render-and-trim workaround is unnecessary.
    # Sign-preserving intensity override: if the preset uses negative intensity
    # (e.g. slow_dolly_out at -0.6), we keep the sign and only swap magnitude.
    if intensity_override is not None:
        sign = -1.0 if p["intensity"] < 0 else 1.0
        effective_intensity = sign * abs(intensity_override)
    else:
        effective_intensity = p["intensity"]

    animation_flags: list[str] = [
        "--intensity",
        str(effective_intensity),
        "--isometric",
        str(p["isometric"]),
    ]
    if p.get("phase") is not None:
        animation_flags += ["--phase", str(p["phase"])]
    effective_steady = steady_override if steady_override is not None else p.get("steady")
    if effective_steady is not None:
        animation_flags += ["--steady", str(effective_steady)]
    # Default to --no-loop unless the preset explicitly opts back in.
    if p.get("loop", False):
        animation_flags += ["--loop"]
    else:
        animation_flags += ["--no-loop"]
    if p.get("linear", False):
        animation_flags += ["--linear"]
    if p.get("reverse", False):
        animation_flags += ["--reverse"]

    args: list[str] = [
        "depthflow",
        "input",
        "-i",
        str(input_image),
        p["animation"],
        *animation_flags,
        "main",
        "--width",
        str(width),
        "--height",
        str(height),
        "--fps",
        str(fps),
        "--time",
        str(duration_sec),
        "--output",
        str(output_video),
    ]

    if extra_cli:
        for k, v in extra_cli.items():
            args.append(f"--{k}")
            args.append(str(v))

    return args


def run_depthflow(
    input_image_path: str | Path,
    output_dir: str | Path,
    *,
    preset: str = "slow_truck",
    duration_sec: int = 6,
    fps: int = 24,
    width: int = 1920,
    height: int = 1080,
    slot_id: str = "ad-hoc",
    template_id: str = "ad-hoc",
    run_id: str | None = None,
    extra_cli: dict[str, Any] | None = None,
    intensity_override: float | None = None,
    steady_override: float | None = None,
    overscan_pct: float = 0.10,
) -> dict[str, Any]:
    """Render a parallax video from a single image via the DepthFlow CLI.

    Args:
        input_image_path: local path to the source image.
        output_dir: where to write the mp4 + metadata.
        preset: name from DEPTHFLOW_PRESETS. 'slow_truck' is the default
            "barely moving but feels cinematic" setting.
        duration_sec / fps / width / height: FINAL output spec (after overscan
            crop). 1920x1080 @ 24fps is the cinematic default.
        slot_id / template_id / run_id: stamped into metadata for audit.
        extra_cli: optional verbatim flag overrides for the depthflow CLI.
        intensity_override: replace the preset's intensity (sign preserved).
        steady_override: replace the preset's --steady value. Lower values
            (0.0 to -0.3) anchor the foreground/focal subject so it stretches
            less; higher values anchor the background.
        overscan_pct: render at larger dimensions then ffmpeg-crop the
            stretched edges out. Default 0.10 (10%): renders at 2112x1188,
            crops back to 1920x1080. Set to 0 to disable.

    Returns:
        Dict with output_path, run_id, preset_used, params, wall_time_sec.
        Mirror of VideoPassResult shape but local-only (no fal URL).
    """
    in_path = Path(input_image_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Input image not found: {in_path}")

    if not _depthflow_available():
        raise DepthFlowError(
            "DepthFlow CLI not found on PATH. Install it as an isolated tool:\n"
            "    pipx install depthflow\n"
            "or grab a portable executable from\n"
            "    https://github.com/BrokenSource/DepthFlow/releases\n"
            "and put it on PATH. We invoke it via subprocess, so it does NOT need\n"
            "to be in this project's venv."
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if run_id is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        run_id = f"{ts}_{slot_id}_depthflow"

    out_path = out_dir / f"{slot_id}_clip.mp4"

    # Overscan: render at larger dimensions, then crop the stretched edges
    # out. Each side gets roughly overscan_pct/2 of the dimension trimmed,
    # so the visible "danger zone" at the edges is discarded entirely.
    if overscan_pct > 0:
        # Round to even numbers — H.264 encoders insist on even dims.
        render_width = int(round(width * (1 + overscan_pct) / 2)) * 2
        render_height = int(round(height * (1 + overscan_pct) / 2)) * 2
        raw_path = out_dir / f"{slot_id}_clip_raw_overscan.mp4"
    else:
        render_width = width
        render_height = height
        raw_path = out_path  # write directly to final, no crop step

    args = _build_cli_args(
        input_image=in_path,
        output_video=raw_path,
        preset=preset,
        duration_sec=duration_sec,
        fps=fps,
        width=render_width,
        height=render_height,
        extra_cli=extra_cli,
        intensity_override=intensity_override,
        steady_override=steady_override,
    )

    print(
        f"  [depthflow] preset={preset} duration={duration_sec}s "
        f"size={width}x{height}@{fps}fps"
    )
    print(f"  [depthflow] $ {' '.join(args)}")

    started = time.time()
    try:
        proc = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min ceiling — should be much faster
        )
    except subprocess.CalledProcessError as e:
        raise DepthFlowError(
            f"DepthFlow CLI failed (exit {e.returncode}).\n"
            f"stderr:\n{e.stderr}\n"
            f"stdout:\n{e.stdout}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise DepthFlowError(
            f"DepthFlow CLI timed out after 300s. "
            f"Likely running on CPU; check that GPU is being used."
        ) from e
    duration_wall = time.time() - started

    if not raw_path.exists():
        raise DepthFlowError(
            f"DepthFlow exited cleanly but no output file at {raw_path}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    # Overscan crop pass — center-crop from render dims down to target.
    # The trimmed pixels (at the frame edges) are exactly the parts where
    # depth-projection stretching is most visible, so cropping them out
    # removes most of the "Photoshop-stretched" artifacts.
    if overscan_pct > 0:
        crop_started = time.time()
        crop_filter = (
            f"crop={width}:{height}:"
            f"(in_w-{width})/2:(in_h-{height})/2"
        )
        ffmpeg_args = [
            "ffmpeg",
            "-y",                          # overwrite without asking
            "-loglevel", "error",
            "-i", str(raw_path),
            "-vf", crop_filter,
            "-c:v", "libx264",
            "-crf", "18",                  # near-lossless quality
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out_path),
        ]
        print(f"  [depthflow] cropping overscan: {render_width}x{render_height} → {width}x{height}")
        try:
            crop_proc = subprocess.run(
                ffmpeg_args,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as e:
            raise DepthFlowError(
                "ffmpeg not found on PATH. Install with `brew install ffmpeg` "
                "or set overscan_pct=0 to skip the crop step."
            ) from e
        except subprocess.CalledProcessError as e:
            raise DepthFlowError(
                f"ffmpeg crop failed (exit {e.returncode}).\nstderr:\n{e.stderr}"
            ) from e
        crop_wall = time.time() - crop_started
        print(f"  [depthflow] crop done in {crop_wall:.1f}s")
        # Keep the raw overscan around for debugging; not auto-deleted.

    if not out_path.exists():
        raise DepthFlowError(
            f"Final output missing at {out_path} after overscan crop."
        )

    metadata = {
        "kind": "depthflow",
        "run_id": run_id,
        "slot_id": slot_id,
        "template_id": template_id,
        "input_image_path": str(in_path),
        "output_path": str(out_path),
        "raw_overscan_path": str(raw_path) if overscan_pct > 0 else None,
        "preset": preset,
        "preset_params": DEPTHFLOW_PRESETS[preset],
        "intensity_override": intensity_override,
        "steady_override": steady_override,
        "overscan_pct": overscan_pct,
        "render_dims": [render_width, render_height],
        "output_dims": [width, height],
        "duration_sec": duration_sec,
        "fps": fps,
        "width": width,
        "height": height,
        "wall_time_sec": round(duration_wall, 2),
        "cli_args": args,
        "extra_cli": extra_cli or {},
    }

    meta_path = out_dir / "depthflow_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, default=str))

    print(f"  [depthflow] done in {duration_wall:.1f}s → {out_path}")
    return metadata


def list_presets() -> list[tuple[str, str]]:
    """Return [(preset_name, notes), ...] for CLI listing."""
    return [(name, p["notes"]) for name, p in DEPTHFLOW_PRESETS.items()]
