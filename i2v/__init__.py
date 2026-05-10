"""i2v — photo-to-video templates.

Public surface:
    - run_image_pass: run a single image-to-image pass via fal
    - run_image_pipeline: run a slot's full multi-pass image chain
    - run_video_pass: run a slot's image-to-video pass via fal
    - load_template, get_slot: load a template JSON and find a slot by id
    - list_known_models / list_known_video_models: enumerate registered models
"""

from i2v.classifier import (
    AssignmentPlan,
    PhotoClassification,
    SlotAssignment,
    assign_to_slots,
    classify_and_assign,
    classify_photo,
    classify_photos,
    resolve_alternate_angles,
)
from i2v.image_pass import run_image_pass, run_image_pipeline
from i2v.models import list_known_models, resolve_model
from i2v.types import (
    ImagePass,
    ImagePassResult,
    ImagePipelineResult,
    Slot,
    Template,
    VideoPass,
    VideoPassResult,
    get_slot,
    load_template,
)
from i2v.video_models import list_known_video_models, resolve_video_model
from i2v.video_pass import run_video_pass
from i2v.video_restyle import run_v2v_restyle
from i2v.video_upscale import run_starlight_upscale

__all__ = [
    "AssignmentPlan",
    "ImagePass",
    "ImagePassResult",
    "ImagePipelineResult",
    "PhotoClassification",
    "Slot",
    "SlotAssignment",
    "Template",
    "VideoPass",
    "VideoPassResult",
    "assign_to_slots",
    "classify_and_assign",
    "classify_photo",
    "classify_photos",
    "get_slot",
    "list_known_models",
    "list_known_video_models",
    "load_template",
    "resolve_alternate_angles",
    "resolve_model",
    "resolve_video_model",
    "run_image_pass",
    "run_image_pipeline",
    "run_starlight_upscale",
    "run_v2v_restyle",
    "run_video_pass",
]
