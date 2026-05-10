"""Job state machine for backgrounded slot/template generation.

Pattern: POST kicks off a FastAPI BackgroundTask, returns a job id; the
frontend polls GET /jobs/{id} every couple seconds until status is `done`
or `error`.

State persists as JSON files in outputs/jobs/<job_id>.json so it survives
backend restarts and the CLI can inspect anything mid-flight. Single-process
hackathon scale; no DB, no Redis.

Worker functions (e.g. run_slot_job below) call our existing pipeline code
verbatim — classify_and_assign, run_image_pipeline, synthesize_end_frame*,
run_video_pass — and surface progress via update_job() between steps.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from i2v.classifier import AssignmentPlan, classify_and_assign
from i2v.end_frame import (
    synthesize_end_frame_multi_ref_inpaint,
    synthesize_end_frame_real_reference,
)
from i2v.image_pass import run_image_pipeline
from i2v.types import Slot, get_slot, load_template
from i2v.video_pass import run_video_pass


# ─── Paths ───────────────────────────────────────────────────────────────────
# Resolved relative to the repo root by api/main.py; we re-resolve here so the
# module is importable from a script too.
REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_ROOT = REPO_ROOT / "inputs"
OUTPUTS_ROOT = REPO_ROOT / "outputs"
TEMPLATES_ROOT = REPO_ROOT / "templates"
JOBS_DIR = OUTPUTS_ROOT / "jobs"

SUPPORTED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
DEFAULT_GENERATIVE_VIDEO_MODEL = "fal-ai/kling-video/v3/pro/image-to-video"
DEFAULT_VIDEO_DURATION_SEC = 6


# ─── Types ───────────────────────────────────────────────────────────────────


JobStatus = Literal[
    "queued",
    "classifying",
    "image_pass",
    "end_frame",
    "video_pass",
    "done",
    "error",
]


class JobRecord(BaseModel):
    """Persisted job state. The frontend polls this verbatim."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["run-slot", "run-template"] = "run-slot"

    project_slug: str
    template_id: str
    slot_id: str | None = None  # None for run-template parents

    status: JobStatus = "queued"
    percent: int = 0
    message: str = "Queued"
    error: str | None = None

    started_at: str
    finished_at: str | None = None

    # Result paths (URLs are relative to backend root, frontend prefixes BACKEND_URL)
    run_dir: str | None = None
    summary_path: str | None = None
    start_frame_url: str | None = None
    end_frame_url: str | None = None
    video_url: str | None = None

    # Inputs / decisions captured for audit + future UI inspection
    primary_photo_path: str | None = None
    end_frame_photo_path: str | None = None
    reference_paths: list[str] = Field(default_factory=list)
    strategy: str | None = None
    model_used: str | None = None


# ─── Storage ────────────────────────────────────────────────────────────────


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    *,
    project_slug: str,
    template_id: str,
    slot_id: str | None = None,
    kind: Literal["run-slot", "run-template"] = "run-slot",
) -> JobRecord:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job = JobRecord(
        id=uuid.uuid4().hex,
        kind=kind,
        project_slug=project_slug,
        template_id=template_id,
        slot_id=slot_id,
        started_at=_now_iso(),
    )
    _job_path(job.id).write_text(job.model_dump_json(indent=2))
    return job


def get_job(job_id: str) -> JobRecord | None:
    p = _job_path(job_id)
    if not p.exists():
        return None
    try:
        return JobRecord.model_validate_json(p.read_text())
    except Exception:
        return None


def update_job(job_id: str, **updates: Any) -> JobRecord | None:
    """Merge updates into the job's JSON. Idempotent; if the job doesn't
    exist (race condition), returns None."""
    job = get_job(job_id)
    if job is None:
        return None
    data = job.model_dump()
    data.update(updates)
    if data.get("status") in ("done", "error") and not data.get("finished_at"):
        data["finished_at"] = _now_iso()
    new = JobRecord.model_validate(data)
    _job_path(job_id).write_text(new.model_dump_json(indent=2))
    return new


# ─── Helpers ────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "house"


def _resolve_project_dir(slug: str) -> Path:
    if not INPUTS_ROOT.exists():
        raise FileNotFoundError(f"inputs root not found: {INPUTS_ROOT}")
    for p in INPUTS_ROOT.iterdir():
        if p.is_dir() and _slugify(p.name) == slug:
            return p
    raise FileNotFoundError(f"Project '{slug}' not found under {INPUTS_ROOT}")


def _project_photos(project_dir: Path) -> list[Path]:
    return sorted(
        f for f in project_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_PHOTO_EXTS
    )


def _photos_hash(photos: list[Path]) -> str:
    """Stable hash of the photo set, used as a plan-cache key."""
    keys = "\n".join(f"{p.name}:{p.stat().st_size}" for p in photos)
    return hashlib.sha256(keys.encode()).hexdigest()[:16]


def _plan_cache_path(project_slug: str) -> Path:
    return OUTPUTS_ROOT / project_slug / "assignments" / "cached_plan.json"


def _resolve_template_path(template_id: str) -> Path:
    if not TEMPLATES_ROOT.exists():
        raise FileNotFoundError("templates dir not found")
    for path in TEMPLATES_ROOT.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("template", {}).get("id") == template_id or path.stem == template_id:
            return path
    raise FileNotFoundError(f"Template '{template_id}' not found")


def _get_or_build_plan(
    *,
    project_slug: str,
    template_id: str,
) -> AssignmentPlan:
    """Return the cached plan if photo set hasn't changed; otherwise classify
    and assign fresh. Plan is keyed by (project_slug, photos_hash) so any
    change to the photo set invalidates the cache."""
    project_dir = _resolve_project_dir(project_slug)
    template = load_template(_resolve_template_path(template_id))
    photos = _project_photos(project_dir)
    if not photos:
        raise ValueError(f"Project '{project_slug}' has no photos")
    photo_hash = _photos_hash(photos)

    cache_path = _plan_cache_path(project_slug)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("_photos_hash") == photo_hash and cached.get("_template_id") == template_id:
                return AssignmentPlan.model_validate(cached["plan"])
        except Exception:
            pass

    plan = classify_and_assign(
        photo_paths=[str(p) for p in photos],
        template=template,
        verbose=False,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "_photos_hash": photo_hash,
                "_template_id": template_id,
                "_cached_at": _now_iso(),
                "plan": plan.model_dump(),
            },
            indent=2,
            default=str,
        )
    )
    return plan


def _to_url(absolute_path: str | Path) -> str | None:
    """Convert an absolute path under outputs/ into a /outputs/... URL the
    static mount can serve. Returns None if the path is not under outputs/."""
    p = Path(absolute_path).resolve()
    try:
        rel = p.relative_to(OUTPUTS_ROOT)
        return f"/outputs/{rel.as_posix()}"
    except ValueError:
        # Not under OUTPUTS_ROOT — could be an inputs/ path.
        try:
            rel = p.relative_to(INPUTS_ROOT)
            return f"/inputs/{rel.as_posix()}"
        except ValueError:
            return None


# ─── The worker — runs in a BackgroundTask thread ───────────────────────────


def run_slot_job(
    job_id: str,
    *,
    project_slug: str,
    template_id: str,
    slot_id: str,
    generative_video_model: str = DEFAULT_GENERATIVE_VIDEO_MODEL,
    video_duration: int = DEFAULT_VIDEO_DURATION_SEC,
) -> None:
    """Synchronous worker that runs one slot end-to-end and threads progress
    back through update_job(). Raises are caught and surfaced as the job's
    error state.

    Mirrors scripts/run_plan_slot.py main(), so behaviour stays consistent
    between CLI and HTTP entry points.
    """
    try:
        update_job(
            job_id,
            status="classifying",
            percent=5,
            message="Loading plan and template",
        )
        plan = _get_or_build_plan(
            project_slug=project_slug,
            template_id=template_id,
        )
        template_path = _resolve_template_path(template_id)
        template = load_template(template_path)
        slot: Slot = get_slot(template, slot_id)

        assignment = next(
            (a for a in plan.slot_assignments if a.slot_id == slot_id),
            None,
        )
        if assignment is None:
            raise RuntimeError(
                f"Slot '{slot_id}' has no assignment in plan for project '{project_slug}'"
            )
        if not assignment.is_active:
            raise RuntimeError(
                f"Slot '{slot_id}' is INACTIVE in plan: {assignment.inactive_reason}"
            )

        update_job(
            job_id,
            percent=20,
            message=f"Strategy: {assignment.end_frame_strategy}",
            primary_photo_path=assignment.primary_photo_path,
            end_frame_photo_path=assignment.end_frame_photo_path,
            reference_paths=list(assignment.additional_reference_photo_paths),
            strategy=assignment.end_frame_strategy,
        )

        # ─── Output directory ───────────────────────────────────────────
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        run_id = f"{ts}_{slot_id}_{uuid.uuid4().hex[:6]}"
        out_dir = OUTPUTS_ROOT / project_slug / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        update_job(job_id, run_dir=str(out_dir))

        # ─── Step 1: image pass on primary ──────────────────────────────
        update_job(
            job_id,
            status="image_pass",
            percent=30,
            message="Editorial enhance on primary photo",
        )
        primary_dir = out_dir / "primary"
        primary_result = run_image_pipeline(
            slot=slot,
            input_image_path=assignment.primary_photo_path,
            output_root=str(primary_dir),
            template_id=template_id,
            with_logs=False,
            reference_photos_override=assignment.additional_reference_photo_paths,
        )
        start_frame_path = primary_result.final_output_path
        update_job(
            job_id,
            percent=50,
            message="Primary frame ready",
            start_frame_url=_to_url(start_frame_path),
        )

        # ─── Step 2: end frame synthesis ────────────────────────────────
        end_frame_path: str | None = None
        if assignment.end_frame_strategy == "real_reference" and assignment.end_frame_photo_path:
            update_job(
                job_id,
                status="end_frame",
                percent=55,
                message="Style-matching the alternate photo as end frame",
            )
            end_dir = out_dir / "end_frame_real"
            end_meta = synthesize_end_frame_real_reference(
                end_frame_photo_path=assignment.end_frame_photo_path,
                slot=slot,
                output_dir=end_dir,
                additional_reference_paths=assignment.additional_reference_photo_paths,
                with_logs=False,
            )
            end_frame_path = end_meta["final_end_frame_path"]
        elif assignment.end_frame_strategy == "multi_ref_inpaint":
            update_job(
                job_id,
                status="end_frame",
                percent=55,
                message="Synthesizing end frame via depthflow extreme",
            )
            end_dir = out_dir / "end_frame_inpaint"
            end_meta = synthesize_end_frame_multi_ref_inpaint(
                start_frame_path=start_frame_path,
                output_dir=end_dir,
                slot_label=slot.label,
                primary_notes=(
                    assignment.primary_classification.notes
                    if assignment.primary_classification
                    else ""
                ),
                additional_reference_paths=assignment.additional_reference_photo_paths,
                with_logs=False,
            )
            end_frame_path = end_meta["final_end_frame_path"]
        # else: depthflow_only / none → no end frame

        if end_frame_path:
            update_job(
                job_id,
                percent=70,
                message="End frame ready",
                end_frame_url=_to_url(end_frame_path),
            )

        # ─── Step 3: video pass ─────────────────────────────────────────
        update_job(
            job_id,
            status="video_pass",
            percent=75,
            message="Generating video clip",
        )
        video_dir = out_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        if end_frame_path:
            video_result = run_video_pass(
                input_image_path=start_frame_path,
                video_pass_spec=slot.video_pass.model_copy(
                    update={
                        "prompt": (
                            f"Smooth cinematic camera move interpolating naturally between "
                            f"two keyframes of the same {slot.label.lower()}. The motion "
                            f"should feel like a real camera tracking from the start view "
                            f"to the end view — not a cut, not a morph. Preserve all "
                            f"architectural details, materials, fixtures, and lighting from "
                            f"both keyframes exactly. No new windows, doors, or fixtures "
                            f"appearing in the interpolated frames. Photorealistic editorial "
                            f"film style, neutral white balance, no abrupt transitions."
                        )
                    }
                ),
                output_dir=video_dir,
                slot_id=slot_id,
                template_id=template_id,
                run_id=run_id,
                end_frame_image_path=end_frame_path,
                with_logs=False,
                model_override=generative_video_model,
                duration_override=video_duration,
                overscan_pct=0.0,
            )
            model_used = generative_video_model
        else:
            video_result = run_video_pass(
                input_image_path=start_frame_path,
                video_pass_spec=slot.video_pass,
                output_dir=video_dir,
                slot_id=slot_id,
                template_id=template_id,
                run_id=run_id,
                with_logs=False,
                duration_override=video_duration,
            )
            model_used = slot.video_pass.model

        # ─── Summary write ──────────────────────────────────────────────
        summary = {
            "run_id": run_id,
            "project_slug": project_slug,
            "template_id": template_id,
            "slot_id": slot_id,
            "strategy": assignment.end_frame_strategy,
            "primary_photo_path": assignment.primary_photo_path,
            "end_frame_photo_path": assignment.end_frame_photo_path,
            "reference_paths": list(assignment.additional_reference_photo_paths),
            "start_frame_path": start_frame_path,
            "end_frame_path": end_frame_path,
            "video_clip_path": video_result.output_path,
            "model_used": model_used,
            "completed_at": _now_iso(),
        }
        summary_path = out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, default=str))

        update_job(
            job_id,
            status="done",
            percent=100,
            message="Done",
            summary_path=_to_url(summary_path),
            video_url=_to_url(video_result.output_path),
            model_used=model_used,
        )
    except Exception as exc:  # broad — anything that breaks becomes user-visible
        tb = traceback.format_exc()
        # Print so it shows up in uvicorn logs too
        print(f"[job {job_id}] ERROR: {exc}\n{tb}")
        update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            message="Failed",
        )
