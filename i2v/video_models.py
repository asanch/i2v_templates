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
    """A registered video model and its input/output adapter.

    Two backends are supported, distinguished by `backend`:
      - "fal"    — runs against fal.ai. Requires build_arguments + extract_output_url.
      - "local"  — runs locally (e.g. DepthFlow). build_arguments / extract_output_url
                   are ignored; the dispatcher in video_pass.py routes to the
                   local handler instead.
    """

    id: str
    label: str
    notes: str
    cost_per_sec: float
    """Advisory list price; not used in submission, only for sanity. 0 for local."""
    min_duration: int
    max_duration: int
    backend: str = "fal"
    """One of {'fal', 'local'}. Controls dispatch in video_pass.py."""
    allowed_durations: tuple[int, ...] | None = None
    """If the model only accepts a discrete set, list them; else any int in [min, max]."""
    supports_end_frame: bool = False
    """Whether the model accepts a tail/end keyframe for arrival/departure shots."""
    default_extra_args: dict[str, Any] = field(default_factory=dict)
    """Verbatim extras to forward — e.g. {'generate_audio': False} for Kling.
    For local backends this can carry preset-specific options (e.g. depthflow preset)."""

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
    """Kling 1.x / 2.x take `image_url` (start) + optional `tail_image_url` (end)
    + `duration` as a string. Older naming convention.
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


def _kling3_build_arguments(
    image_url: str,
    prompt: str,
    duration_sec: int,
    end_frame_url: str | None,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """Kling 3.x / O-series renamed the keyframe params: `start_image_url` +
    optional `end_image_url`. The old `image_url`/`tail_image_url` are
    silently ignored — that's why our pipeline appeared to lose end frames
    on Kling 3.0 Pro before this fix.
    """
    args: dict[str, Any] = {
        "prompt": prompt,
        "start_image_url": image_url,
        "duration": str(duration_sec),
    }
    if end_frame_url:
        args["end_image_url"] = end_frame_url
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
    """Seedance v1/v1.5: `image_url` + `prompt` + `duration` (int).
    No end-frame support on v1.x.
    """
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": duration_sec,
    }
    args.update(extras)
    return args


def _seedance2_build_arguments(
    image_url: str,
    prompt: str,
    duration_sec: int,
    end_frame_url: str | None,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """Seedance 2.0 image-to-video: `image_url` (start) + optional
    `end_image_url` (tail) + `prompt` + `duration`.

    Different from Kling's `tail_image_url` naming and from v1.x's
    no-tail-support shape. Per fal docs (April 2026 release).
    """
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": duration_sec,
    }
    if end_frame_url:
        args["end_image_url"] = end_frame_url
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
            "for arrival/departure shots. fal accepts only '5' or '10' second "
            "durations. ~$0.07/sec, ~60–90s wall time."
        ),
        cost_per_sec=0.07,
        min_duration=5,
        max_duration=10,
        allowed_durations=(5, 10),
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
            "for shots where the camera move is simple. ~$0.035/sec. "
            "fal accepts only '5' or '10' second durations."
        ),
        cost_per_sec=0.035,
        min_duration=5,
        max_duration=10,
        allowed_durations=(5, 10),
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
            "viewer will judge hardest. ~$0.22/sec; 90–150s wall time. "
            "fal accepts only '5' or '10' second durations. NOTE: Kling 3.x uses "
            "`start_image_url` + `end_image_url` (not Kling 2.x's `image_url` + "
            "`tail_image_url`)."
        ),
        cost_per_sec=0.22,
        min_duration=5,
        max_duration=10,
        allowed_durations=(5, 10),
        supports_end_frame=True,
        default_extra_args={"generate_audio": False},
        build_arguments=_kling3_build_arguments,
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
            "Older Seedance generation. Tighter motion, less drift than Kling 2.x "
            "on complex scenes. Superseded by Seedance 2.0 — keep for A/B only."
        ),
        cost_per_sec=0.10,
        min_duration=5,
        max_duration=10,
        allowed_durations=(5, 10),
        supports_end_frame=False,
        default_extra_args={},
        build_arguments=_seedance_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    VideoModelAdapter(
        id="bytedance/seedance-2.0/image-to-video",
        label="Seedance 2.0 (ByteDance, image-to-video)",
        notes=(
            "Latest Seedance. Drop-in alternative to Kling 3.0 Pro for the start "
            "+ optional end-frame interpolation case. Strong on natural motion "
            "and architectural consistency. ~$0.10/sec. Use as A/B against Kling "
            "3.0; pick whichever gives better motion on your test set. Note: "
            "uses `end_image_url` (not Kling's `tail_image_url`); fal route is "
            "`bytedance/seedance-2.0/...` (no `fal-ai/` prefix, hyphen-2.0 "
            "naming — different convention from v1/v1.5)."
        ),
        cost_per_sec=0.10,
        min_duration=5,
        max_duration=10,
        allowed_durations=(5, 10),
        supports_end_frame=True,
        default_extra_args={},
        build_arguments=_seedance2_build_arguments,
        extract_output_url=_default_extract_video_url,
    ),
    # NOTE: Seedance 2.0 reference-to-video (up to 9 reference images via
    # @Image1..@Image9 prompt tags) is the strategically right model for
    # multi-reference architectural anchoring, but using it requires
    # extending the VideoPass schema with `reference_images` so the runner
    # can thread the photo list through. Once that lands, register
    # `fal-ai/bytedance/seedance/v2.0/reference-to-video` here.

    # ─── Local backends — depth-projected parallax, no generative video ─────
    # These run on Aaron's machine via the DepthFlow CLI. Architecture is
    # guaranteed because we're reprojecting real pixels through depth, not
    # generating new ones. Default for any "just a camera move" slot.

    VideoModelAdapter(
        id="local/depthflow/slow_truck",
        label="DepthFlow — Slow Truck (local 2.5D parallax)",
        notes=(
            "Local depth-projected parallax. Subtle horizontal lateral motion (~8% "
            "of frame). DEFAULT for wide interior shots — kitchen wide, living wide. "
            "Architecture is guaranteed by depth reprojection, not preserved by prompt. "
            "Free, ~10–30s render on Apple Silicon."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "slow_truck"},
    ),
    VideoModelAdapter(
        id="local/depthflow/slow_dolly_in",
        label="DepthFlow — Slow Dolly In",
        notes=(
            "Subtle forward push (~6% of frame). DEFAULT for hero exterior, entry. "
            "Camera moves toward the subject without inventing geometry."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "slow_dolly_in"},
    ),
    VideoModelAdapter(
        id="local/depthflow/slow_dolly_out",
        label="DepthFlow — Slow Dolly Out / Pull Back",
        notes=(
            "Subtle pull-back. Default for closing hero exterior shot — reveals "
            "the property without inventing what's behind the camera."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "slow_dolly_out"},
    ),
    VideoModelAdapter(
        id="local/depthflow/orbit_subtle",
        label="DepthFlow — Subtle Orbit",
        notes=(
            "Gentle orbit around a focal point. Good for hero exterior or any shot "
            "where the photo has clear foreground/background depth separation."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "orbit_subtle"},
    ),
    VideoModelAdapter(
        id="local/depthflow/medium_truck",
        label="DepthFlow — Medium Truck (more motion)",
        notes=(
            "Pronounced horizontal lateral parallax. Use when slow_truck reads as "
            "too still and you want clearly cinematic motion. Stronger depth separation "
            "(isometric 0.85)."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "medium_truck"},
    ),
    VideoModelAdapter(
        id="local/depthflow/medium_dolly_in",
        label="DepthFlow — Medium Dolly In (more motion)",
        notes=(
            "Pronounced forward push. Use for hero exterior or any shot where "
            "slow_dolly_in feels too tame."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "medium_dolly_in"},
    ),
    VideoModelAdapter(
        id="local/depthflow/static_with_light_drift",
        label="DepthFlow — Near-Static (base for masked motion)",
        notes=(
            "Near-still parallax with minimal drift. Designed as the 'rule' layer "
            "underneath a generative 'exception' overlay (e.g. moving water, "
            "shifting light). Not a finished shot on its own."
        ),
        cost_per_sec=0.0,
        min_duration=2,
        max_duration=15,
        backend="local",
        supports_end_frame=False,
        default_extra_args={"preset": "static_with_light_drift"},
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
