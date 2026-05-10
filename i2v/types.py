"""Pydantic types matching the template JSON schema.

These types are the single source of truth for what's in a template. The CLI
loads JSON, validates it through these types, and the rest of the codebase
only ever sees validated `Template` / `Slot` / `ImagePass` objects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Image-pipeline types ────────────────────────────────────────────────────


PassSource = Literal["input_photo", "previous_pass"]
"""
Where a pass's input image comes from.
- `input_photo`: the original user-supplied photo
- `previous_pass`: the output of the immediately preceding pass in the chain
"""


class ImagePass(BaseModel):
    """One image-to-image pass in a slot's image pipeline."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="Human-readable tag for this pass; used in output filenames "
        "(e.g. 'editorial_enhance', 'recompose_detail')."
    )
    source: PassSource = Field(
        default="input_photo",
        description="Where this pass's PRIMARY input image comes from. The first pass "
        "should almost always be 'input_photo'; subsequent passes typically chain "
        "from 'previous_pass'. The primary is the edit target.",
    )
    model: str = Field(
        description="fal model id, e.g. 'fal-ai/nano-banana/edit'. See i2v.models for "
        "known presets and how each is adapted."
    )
    prompt: str
    negative_prompt: str | None = None
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Forwarded to the model adapter. Common keys: aspect_ratio, "
        "output_format, num_images, image_size, temperature.",
    )
    reference_photos: list[str] = Field(
        default_factory=list,
        description=(
            "Optional architecture-anchor photos passed alongside the primary source. "
            "These are NOT edit targets — they only condition the model so that walls, "
            "fixtures, materials, and layout from the primary view are preserved. "
            "Best when they're real photos of the same room from different angles. "
            "Per Seedance/Nano Banana research: 3–5 focused references beat 9 random "
            "ones. Capped by max_references."
        ),
    )
    max_references: int = Field(
        default=4,
        ge=0,
        le=8,
        description=(
            "Cap on how many references to pass to the model. 3–4 is the sweet spot; "
            "more dilutes the primary's priority. Set to 0 to ignore reference_photos "
            "entirely for this pass."
        ),
    )

    @field_validator("label")
    @classmethod
    def _label_is_filesystem_safe(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(
                "ImagePass.label must be non-empty and contain only alphanumerics, '-', '_'"
            )
        return v


class VideoPass(BaseModel):
    """Spec for the image-to-video pass — used by the next phase, not by image_pass.py."""

    model_config = ConfigDict(extra="forbid")

    model: str
    prompt: str
    duration_sec: int
    start_frame_source: Literal["image_pipeline.last", "image_pipeline.first"] = (
        "image_pipeline.last"
    )
    end_frame_source: str | None = None
    extra_args: dict[str, Any] = Field(default_factory=dict)


class ClipPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim_in_ms: int = 0
    trim_out_ms: int = 0
    speed: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0


# ─── Photo-requirement / slot ────────────────────────────────────────────────


SceneKind = Literal[
    "exterior_front",
    "exterior_back",
    "exterior_aerial",
    "entry",
    "hallway",
    "stairs",
    "kitchen",
    "dining",
    "living",
    "office",
    "media_room",
    "primary_bed",
    "secondary_bed",
    "primary_bath",
    "secondary_bath",
    "outdoor_yard",
    "outdoor_pool",
    "outdoor_patio",
    "detail_architectural",
    "detail_material",
    "detail_window",
    "view_window",
]


class PhotoRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_kind: SceneKind
    intent: str
    composition_hints: list[str] = Field(default_factory=list)
    ideal_time_of_day: str = "any"


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    order: int
    required: bool
    photo_requirement: PhotoRequirement
    image_pipeline: list[ImagePass] = Field(min_length=1, max_length=4)
    video_pass: VideoPass | None = None
    clip_post: ClipPost = Field(default_factory=ClipPost)


# ─── Template ────────────────────────────────────────────────────────────────


class TemplateMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    author: str | None = None
    description: str | None = None
    reference_url: str | None = None
    duration_sec: float
    aspect_ratio: Literal["16:9", "9:16", "4:5", "1:1"]
    resolution: str
    fps: Literal[24, 25, 30, 60]
    # UI metadata — optional. `thumbnail` is a path or URL the studio gallery
    # tile renders. `enabled` lets us ship stub templates that show in the
    # gallery for the demo but don't actually run.
    thumbnail: str | None = None
    enabled: bool = True


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_slot: str
    kind: Literal["cut", "fade_to_black", "dissolve", "fade_through_white"]
    duration_ms: int = 0


class Music(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_path: str
    track_credit: str | None = None
    start_offset_ms: int = 0
    master_volume_db: float = -6.0
    fade_in_ms: int = 800
    fade_out_ms: int = 1200


class Post(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_lut: str | None = None
    title_card: dict[str, Any] | None = None
    watermark: str | None = None


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    template: TemplateMeta
    slots: list[Slot] = Field(min_length=1, max_length=30)
    transitions: list[Transition] = Field(default_factory=list)
    music: Music
    post: Post = Field(default_factory=Post)


# ─── Loaders / lookups ───────────────────────────────────────────────────────


def load_template(path: str | Path) -> Template:
    """Load and validate a template JSON file."""
    raw = json.loads(Path(path).read_text())
    return Template.model_validate(raw)


def get_slot(template: Template, slot_id: str) -> Slot:
    """Find a slot by id; raise KeyError with a helpful list if not found."""
    for slot in template.slots:
        if slot.id == slot_id:
            return slot
    available = ", ".join(s.id for s in template.slots)
    raise KeyError(f"Slot '{slot_id}' not found in template. Available slots: {available}")


# ─── Result types ────────────────────────────────────────────────────────────


class ImagePassResult(BaseModel):
    """The result of running a single image pass."""

    model_config = ConfigDict(extra="forbid")

    pass_index: int
    pass_label: str
    model: str
    prompt: str
    parameters: dict[str, Any]
    input_path: str
    reference_paths: list[str] = Field(
        default_factory=list,
        description="Architecture-anchor photos that were passed alongside the primary "
        "input. Empty if the pass was single-image. Stamped for audit so we can "
        "reproduce a successful run exactly.",
    )
    output_path: str
    output_url: str | None = None
    duration_sec: float
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ImagePipelineResult(BaseModel):
    """The result of running a slot's full image pipeline."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str
    template_id: str
    run_id: str
    output_dir: str
    passes: list[ImagePassResult]

    @property
    def final_output_path(self) -> str:
        """Path to the last pass's output — what feeds into the video pass."""
        return self.passes[-1].output_path


class VideoPassResult(BaseModel):
    """The result of running a slot's video pass."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str
    template_id: str
    run_id: str
    model: str
    prompt: str
    duration_sec: int
    input_image_path: str
    end_frame_image_path: str | None = None
    output_path: str
    output_url: str | None = None
    duration_wall_sec: float
    extra_args: dict[str, Any] = Field(default_factory=dict)
