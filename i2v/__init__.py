"""i2v — photo-to-video templates.

Public surface:
    - run_image_pass: run a single image-to-image pass via fal
    - run_image_pipeline: run a slot's full multi-pass image chain
    - run_video_pass: run a slot's image-to-video pass via fal
    - load_template, get_slot: load a template JSON and find a slot by id
    - list_known_models / list_known_video_models: enumerate registered models
"""

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

__all__ = [
    "ImagePass",
    "ImagePassResult",
    "ImagePipelineResult",
    "Slot",
    "Template",
    "VideoPass",
    "VideoPassResult",
    "get_slot",
    "list_known_models",
    "list_known_video_models",
    "load_template",
    "resolve_model",
    "resolve_video_model",
    "run_image_pass",
    "run_image_pipeline",
    "run_video_pass",
]
