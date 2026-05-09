"""Video-model registry + per-model adapters.

Mirrors i2v/models.py but for image-to-video models on fal. Each adapter
normalizes input shape (since each model has slightly different argument
names — `duration` vs `tail_image_url` vs `negative_prompt`) and extracts
the output URL from the response (most return {"video": {"url": ...}}).

To add a new video model:
  1. Append a `VideoModelAdapter` to KNOWN_VIDEO_MODELS.
  2. If its input/output shape differs from the defaults, override
     build_arguments / extract_output_url.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class VideoModelAdapter:
    """A registered fal image-to-video model and its input/output adapter."""

    id: str
    label: str
    notes: str
    cost_per_sec: float
    """Advisory list price; not used in submission, only for sanity."""
    min_duration: int
    max_duration: int
    allowed_durations: tuple[int, ...] | None = None
    """If the model only accepts a discrete set, list them; else any int in [min, max]."""
    supports_end_frame: bool = False
    """Whether the model accepts a tail/end keyframe for arrival/departure shots."""
    default_extra_args: dict[str, Any] = field(default_factory=dict)
    """Verbatim extras to forward — e.g. {'generate_audio': False} for Kling."""

    build_arguments: Callable[
        [str, str, int, str | None, dict[str, Any]], dict[str, Any]
    ] = field(default=None)  # type: ignore[assignment]
    extract_output_url: Callable[[dict[str, Any]], str] = field(default=None)  # type: ignore[assignment]

    def clamp_duration(self, requested: int) -> int:
        """Snap a requested duration to what this model actually supports."""
        if self.allowed_durations:
            return min(self.allowed_durations, key=lambda d: abs(d - requested))
        return max(self.min_duration, min(self.max_duration, requested))


# ─── Default adapters ────────────────────────────────────────────────────────


def _kling_build_arguments(
    image_url: str,
    prompt: str,
    duration_sec: int,
    end_frame_url: str | None,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """Kling models take `image_url` (start) + optional `tail_image_url` (end) +
    `duration` as a string.
    """
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": str(duration_sec),
    }
    if end_frame_url:
        args["tail_image_url"] = end_frame_url
    args.update(extras)
    return args


def _veo3_build_arguments(
    image_url: str,
    prompt: str,
    duration_sec: int,
    end_frame_url: str | None,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """Veo 3 takes `image_url` + `prompt` + `duration` like '6s'."""
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": f"{duration_sec}s",
    }
    args.update(extras)
    return args


def _seedance_build_arguments(
    image_url: str,
    prompt: str,
    duration_sec: int,
    end_frame_url: str | None,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """Seedance pro: `image_url` + `prompt` + `duration` (int)."""
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": duration_sec,
    }
    args.update(extras)
    return args


def _default_extract_video_url(response: dict[str, Any]) -> str:
    """Most fal video models return {'video': {'url': '...'}}.

    Some return {'videos': [{'url': '...'}]} (plural); fall back to that.
    """
    if isinstance(response.get("video"), dict) and "url" in response["video"]:
        return response["video"]["url"]
    if isinstance(response.get("videos"), list) and response["videos"]:
        first = response["videos"][0]
        if isinstance(first, dict) and "url" in first:
            return first["url"]
    raise ValueError(
        f"Could not extract video URL from response. Top-level keys: {list(response.keys())}"
    )


# ─── Registry ────────────────────────────────────────────────────────────────


KNOWN_VIDEO_MODELS: tuple[VideoModelAdapter, ...] = (
    VideoModelAdapter(
        id="fal-ai/kling-video/v2.6/pro/image-to-video",
        label="Kling 2.6 Pro (image-to-video)",
        notes=(
            "DEFAULT for cinematic real-estate clips. Strong on smooth dolly/truck "
            "moves and time-lapse light progression. Supports start + end keyframes "
            "for arrival/departure shots. ~$0.07/sec, 5–10s clips, ~60–90s wall time."
        ),
        cost_per_sec=0.07,
        min_duration=3,
        max_duration=10,
        supports_end_frame=True,
        default_extra_args={"generate_audio": False},
        build_arguments=_kling_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    VideoModelAdapter(
        id="fal-ai/kling-video/v2.6/standard/image-to-video",
        label="Kling 2.6 Standard (image-to-video)",
        notes=(
            "Cheaper Kling variant. Slightly less polished motion than Pro but fine "
            "for shots where the camera move is simple. ~$0.035/sec."
        ),
        cost_per_sec=0.035,
        min_duration=3,
        max_duration=10,
        supports_end_frame=True,
        default_extra_args={"generate_audio": False},
        build_arguments=_kling_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    VideoModelAdapter(
        id="fal-ai/kling-video/v3/pro/image-to-video",
        label="Kling 3.0 Pro (image-to-video)",
        notes=(
            "Highest-quality Kling tier. Best for the hero exterior and any shot a "
            "viewer will judge hardest. ~$0.22/sec; 90–150s wall time."
        ),
        cost_per_sec=0.22,
        min_duration=3,
        max_duration=15,
        supports_end_frame=True,
        default_extra_args={"generate_audio": False},
        build_arguments=_kling_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    VideoModelAdapter(
        id="fal-ai/veo3",
        label="Veo 3.1 (Google, image-to-video)",
        notes=(
            "Distinct visual character — softer light, more natural camera motion. "
            "Discrete durations only (4/6/8s). No end-frame support; use for shots "
            "that don't need a planned arrival. ~$0.20/sec."
        ),
        cost_per_sec=0.20,
        min_duration=4,
        max_duration=8,
        allowed_durations=(4, 6, 8),
        supports_end_frame=False,
        default_extra_args={},
        build_arguments=_veo3_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    VideoModelAdapter(
        id="fal-ai/bytedance/seedance/v1/pro/image-to-video",
        label="Seedance 1 Pro (ByteDance, image-to-video)",
        notes=(
            "ByteDance's video model. Tighter motion, less drift than Kling on "
            "complex scenes. Worth A/B'ing on the kitchen detail shot where the "
            "smooth tracking matters."
        ),
        cost_per_sec=0.10,
        min_duration=3,
        max_duration=10,
        supports_end_frame=False,
        default_extra_args={},
        build_arguments=_seedance_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
)


def list_known_video_models() -> list[VideoModelAdapter]:
    return list(KNOWN_VIDEO_MODELS)


def resolve_video_model(model_id: str) -> VideoModelAdapter:
    for m in KNOWN_VIDEO_MODELS:
        if m.id == model_id:
            return m
    known = "\n".join(f"  - {m.id}" for m in KNOWN_VIDEO_MODELS)
    raise ValueError(
        f"Video model '{model_id}' is not in the registry. Known video models:\n{known}\n"
        f"Add it to i2v/video_models.py to use it."
    )
