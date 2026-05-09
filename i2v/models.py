"""Image-model registry + per-model adapters.

Each registered model has:
  - `id`: the fal route, e.g. 'fal-ai/nano-banana/edit'
  - `label` / `notes`: human-readable description
  - `default_for`: the kind of work this model is the best default for
  - `build_arguments(image_url, prompt, parameters)`: produces the dict to pass
    to fal_client.subscribe — this is where per-model input-shape differences
    get normalised.
  - `extract_output_url(response)`: pulls the result image URL out of the
    fal response dict — most models return {"images": [{"url": ...}]} but a
    few don't, so we route through the adapter.

To add a new model:
  1. Append a `ModelAdapter` to KNOWN_MODELS.
  2. If its input or output shape differs from the default, override
     `build_arguments` / `extract_output_url`.
That's it. The rest of the codebase stays the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ─── Adapter type ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelAdapter:
    """A registered image-edit model and its fal-input/output adapter."""

    id: str
    label: str
    notes: str
    default_for: tuple[str, ...] = ()
    """Tags describing what this model is the recommended default for, e.g.
    ('cinematic_enhance', 'detail_recompose'). Surface-level; not used to
    auto-route — humans pick models in the template, this is just hints."""

    build_arguments: Callable[[list[str], str, dict[str, Any]], dict[str, Any]] = field(
        default=None  # type: ignore[assignment]
    )
    extract_output_url: Callable[[dict[str, Any]], str] = field(default=None)  # type: ignore[assignment]


# ─── Default adapters (most models conform to these) ─────────────────────────


def _default_build_arguments(
    image_urls: list[str], prompt: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Default fal input shape: `image_urls` (list) + `prompt` + passthrough params.

    Used by Nano Banana, Seedream Edit, Qwen Edit, etc. — the modern fal i2i
    convention.
    """
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_urls": image_urls,
        "num_images": 1,
    }
    # Passthrough common parameters; per-model adapters can override before this
    for k in ("aspect_ratio", "output_format", "image_size", "guidance_scale", "seed"):
        if k in parameters:
            args[k] = parameters[k]
    return args


def _flux_build_arguments(
    image_urls: list[str], prompt: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """FLUX Kontext takes a single `image_url` (not a list)."""
    if not image_urls:
        raise ValueError("FLUX Kontext requires at least one input image.")
    args: dict[str, Any] = {
        "prompt": prompt,
        "image_url": image_urls[0],
        "num_images": 1,
    }
    for k in ("aspect_ratio", "output_format", "guidance_scale", "seed", "safety_tolerance"):
        if k in parameters:
            args[k] = parameters[k]
    return args


def _default_extract_output_url(response: dict[str, Any]) -> str:
    """Most fal i2i models return {'images': [{'url': '...'}]}.

    Fall back to {'image': {'url': '...'}} for older variants.
    """
    if isinstance(response.get("images"), list) and response["images"]:
        first = response["images"][0]
        if isinstance(first, dict) and "url" in first:
            return first["url"]
    if isinstance(response.get("image"), dict) and "url" in response["image"]:
        return response["image"]["url"]
    raise ValueError(
        f"Could not extract output URL from response. Top-level keys: {list(response.keys())}"
    )


# ─── Registry ────────────────────────────────────────────────────────────────


KNOWN_MODELS: tuple[ModelAdapter, ...] = (
    ModelAdapter(
        id="fal-ai/nano-banana/edit",
        label="Nano Banana (Gemini 2.5 Flash Image — edit)",
        notes=(
            "DEFAULT for cinematic enhance and recompose. Reference model for the "
            "AutoHDR creator-tuned prompts; strong identity preservation, fast (~5–10s), "
            "cheap (~$0.039/image). Use this first."
        ),
        default_for=("cinematic_enhance", "detail_recompose", "perspective_correct"),
        build_arguments=_default_build_arguments,
        extract_output_url=_default_extract_output_url,
    ),
    ModelAdapter(
        id="fal-ai/flux-pro/kontext",
        label="FLUX.1 Kontext Pro (image-edit)",
        notes=(
            "Black Forest Labs' editing model. Excellent at long structured prompts; "
            "stronger than Nano Banana on subject relocation and complex compositional "
            "edits. Slightly slower (~10–20s). Solid A/B alternate when Nano Banana's "
            "output looks artificial."
        ),
        default_for=("cinematic_enhance", "complex_edit"),
        build_arguments=_flux_build_arguments,
        extract_output_url=_default_extract_output_url,
    ),
    ModelAdapter(
        id="fal-ai/flux-pro/kontext/max",
        label="FLUX.1 Kontext Max (image-edit, higher quality)",
        notes=(
            "Higher-quality variant of Kontext Pro. Slower (~20–40s) and more expensive "
            "(~$0.08/image). Worth using on the hero exterior shot where viewers judge "
            "hardest. Overkill for interior detail work."
        ),
        default_for=("hero_exterior", "complex_edit"),
        build_arguments=_flux_build_arguments,
        extract_output_url=_default_extract_output_url,
    ),
    ModelAdapter(
        id="fal-ai/bytedance/seedream/v4/edit",
        label="Seedream 4 Edit (ByteDance)",
        notes=(
            "Strong photorealism and texture fidelity (stone, wood, fabric, bokeh). "
            "Identity preservation can drift on whole-room edits — risky for wides — but "
            "shines on tight detail recomposes (the 85mm pass). A/B against Nano Banana "
            "for slot 06."
        ),
        default_for=("detail_recompose", "texture_close_up"),
        build_arguments=_default_build_arguments,
        extract_output_url=_default_extract_output_url,
    ),
    ModelAdapter(
        id="fal-ai/qwen-image-edit",
        label="Qwen Image Edit (Alibaba)",
        notes=(
            "Strong at object-level edits (add/remove/swap/declutter). Less tuned for "
            "global tonal restructuring. Reserve for staging work in v2 (e.g. 'remove "
            "the laundry basket', 'swap dining chairs') — not the cinematic enhance pass."
        ),
        default_for=("declutter", "object_swap"),
        build_arguments=_default_build_arguments,
        extract_output_url=_default_extract_output_url,
    ),
)


def list_known_models() -> list[ModelAdapter]:
    """Return the registry as a list, in declaration order."""
    return list(KNOWN_MODELS)


def resolve_model(model_id: str) -> ModelAdapter:
    """Look up a registered model by its fal id. Raises ValueError if unknown.

    Unknown models are rejected on purpose — if you want to add one, register it
    in this file. That keeps the input-shape adapter explicit and prevents quiet
    breakage when fal updates a route's schema.
    """
    for m in KNOWN_MODELS:
        if m.id == model_id:
            return m
    known = "\n".join(f"  - {m.id}" for m in KNOWN_MODELS)
    raise ValueError(
        f"Model '{model_id}' is not in the registry. Known models:\n{known}\n"
        f"Add it to i2v/models.py to use it."
    )
