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
import subprocess
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from i2v.classifier import AssignmentPlan, classify_and_assign
from i2v.end_frame import (
    synthesize_end_frame_multi_ref_inpaint,
    synthesize_end_frame_real_reference,
)
from i2v.image_pass import run_image_pipeline
from i2v.types import Slot, Template, get_slot, load_template
from i2v.video_pass import run_video_pass


# ─── Paths ───────────────────────────────────────────────────────────────────
# Resolved relative to the repo root by api/main.py; we re-resolve here so the
# module is importable from a script too.
REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_ROOT = REPO_ROOT / "inputs"
OUTPUTS_ROOT = REPO_ROOT / "outputs"
TEMPLATES_ROOT = REPO_ROOT / "templates"
AUDIO_ROOT = REPO_ROOT / "audio"
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
    "concatenating",
    "muxing_audio",
    "done",
    "error",
]


class JobRecord(BaseModel):
    """Persisted job state. The frontend polls this verbatim."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["run-slot", "run-template", "classify"] = "run-slot"

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

    # Template-export specific fields (kind == "run-template")
    export_video_url: str | None = None  # final concatenated mp4
    export_thumbnail_url: str | None = None  # poster jpg from first slot
    audio_track: str | None = None  # filename of mux'd audio, if any
    slot_video_urls: list[str] = Field(default_factory=list)  # in concat order


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
    kind: Literal["run-slot", "run-template", "classify"] = "run-slot",
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


class _SlotRenderResult(BaseModel):
    """Bundle of paths produced by `_render_slot_pipeline`. Both the
    one-shot run-slot worker and the multi-slot run-template worker consume
    this so the rendering stages stay defined in exactly one place."""

    model_config = ConfigDict(extra="forbid")
    slot_id: str
    run_id: str
    out_dir: str
    start_frame_path: str
    end_frame_path: str | None
    video_clip_path: str
    model_used: str
    strategy: str
    primary_photo_path: str | None
    end_frame_photo_path: str | None
    reference_paths: list[str]
    summary_path: str


def _render_slot_pipeline(
    *,
    project_slug: str,
    template_id: str,
    slot_id: str,
    plan: AssignmentPlan,
    template: Template,
    image_model: str | None = None,
    generative_video_model: str | None = None,
    video_duration: int = DEFAULT_VIDEO_DURATION_SEC,
    disable_end_frame: bool = False,
    progress: Callable[[str, int, str, dict | None], None] | None = None,
) -> _SlotRenderResult:
    """Render one slot end-to-end. Internal helper shared by `run_slot_job`
    and `run_template_job`. Surfaces stage transitions through `progress`
    (status, percent, message, extra-fields-dict).

    Model overrides:
      • image_model: when set, every image pass uses this fal id instead
        of the per-pass template default. None → template default per pass.
      • generative_video_model: when set, EVERY slot uses this video model
        regardless of strategy (overrides DepthFlow presets too). When None,
        Kling 3.0 Pro is used for keyframe-interpolation slots and the
        slot's template-defined model is used otherwise.
      • disable_end_frame: when True, skip end-frame synthesis entirely and
        send only the start frame to the video model. Used by the per-slot
        "Skip end frame" toggle in the studio when a slot's interpolation
        between start↔end is producing artifacts.
    """

    def _emit(status: str, pct: int, msg: str, extra: dict | None = None) -> None:
        if progress is not None:
            progress(status, pct, msg, extra)

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

    _emit(
        "image_pass",
        20,
        f"Strategy: {assignment.end_frame_strategy}",
        {
            "primary_photo_path": assignment.primary_photo_path,
            "end_frame_photo_path": assignment.end_frame_photo_path,
            "reference_paths": list(assignment.additional_reference_photo_paths),
            "strategy": assignment.end_frame_strategy,
        },
    )

    # ─── Output directory ───────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_id = f"{ts}_{slot_id}_{uuid.uuid4().hex[:6]}"
    out_dir = OUTPUTS_ROOT / project_slug / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit("image_pass", 25, "Editorial enhance on primary photo", {"run_dir": str(out_dir)})

    # ─── Step 1: image pass on primary ──────────────────────────────
    primary_dir = out_dir / "primary"
    primary_result = run_image_pipeline(
        slot=slot,
        input_image_path=assignment.primary_photo_path,
        output_root=str(primary_dir),
        template_id=template_id,
        with_logs=False,
        model_override=image_model,
        reference_photos_override=assignment.additional_reference_photo_paths,
    )
    start_frame_path = primary_result.final_output_path
    _emit(
        "image_pass",
        50,
        "Primary frame ready",
        {"start_frame_url": _to_url(start_frame_path)},
    )

    # ─── Step 2: end frame synthesis ────────────────────────────────
    end_frame_path: str | None = None
    if disable_end_frame:
        _emit(
            "end_frame",
            55,
            "End frame skipped (per-slot override) — using start frame only",
        )
    elif assignment.end_frame_strategy == "real_reference" and assignment.end_frame_photo_path:
        _emit("end_frame", 55, "Style-matching the alternate photo as end frame")
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
        _emit("end_frame", 55, "Synthesizing end frame via depthflow extreme")
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
        _emit(
            "end_frame",
            70,
            "End frame ready",
            {"end_frame_url": _to_url(end_frame_path)},
        )

    # ─── Step 3: video pass ─────────────────────────────────────────
    _emit("video_pass", 75, "Generating video clip")
    video_dir = out_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    if end_frame_path:
        # Keyframe-interpolation path. User override wins; otherwise use the
        # default generative model (Kling 3.0 Pro).
        video_model_id = generative_video_model or DEFAULT_GENERATIVE_VIDEO_MODEL
        video_result = run_video_pass(
            input_image_path=start_frame_path,
            video_pass_spec=slot.video_pass.model_copy(
                update={
                    "prompt": (
                        # Anti-handheld language is load-bearing here. The
                        # earlier wording ("feel like a real camera tracking")
                        # was making Kling 3.0 produce phone-in-hand bounce —
                        # vertical wobble at walking cadence, slight roll on
                        # every step. Explicit "dolly on a fixed rail or
                        # tripod-stabilized slider" + the negative list
                        # cleared it up.
                        f"Smooth cinematic dolly move on a fixed rail or "
                        f"tripod-stabilized slider, interpolating between two "
                        f"keyframes of the same {slot.label.lower()}. The "
                        f"camera is mechanically stabilized: NO handheld "
                        f"motion, NO camera shake, NO walking footstep "
                        f"cadence, NO vertical bounce, NO rotational wobble, "
                        f"NO rolling-shutter jello. The motion is a single "
                        f"continuous translation (or smooth dolly arc) at "
                        f"constant velocity from the start view to the end "
                        f"view — not a cut, not a morph. Preserve all "
                        f"architectural details, materials, fixtures, and "
                        f"lighting from both keyframes exactly. No new "
                        f"windows, doors, or fixtures appearing in the "
                        f"interpolated frames. Photorealistic editorial film "
                        f"style, neutral white balance, no abrupt transitions."
                    )
                }
            ),
            output_dir=video_dir,
            slot_id=slot_id,
            template_id=template_id,
            run_id=run_id,
            end_frame_image_path=end_frame_path,
            with_logs=False,
            model_override=video_model_id,
            duration_override=video_duration,
            overscan_pct=0.0,
        )
        model_used = video_model_id
    else:
        # No-end-frame path: typically a DepthFlow preset. If the user
        # explicitly picked a video model in the UI, force-override the
        # template's per-slot model so their choice applies everywhere.
        video_result = run_video_pass(
            input_image_path=start_frame_path,
            video_pass_spec=slot.video_pass,
            output_dir=video_dir,
            slot_id=slot_id,
            template_id=template_id,
            run_id=run_id,
            with_logs=False,
            model_override=generative_video_model,
            duration_override=video_duration,
        )
        model_used = generative_video_model or slot.video_pass.model

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

    return _SlotRenderResult(
        slot_id=slot_id,
        run_id=run_id,
        out_dir=str(out_dir),
        start_frame_path=str(start_frame_path),
        end_frame_path=str(end_frame_path) if end_frame_path else None,
        video_clip_path=str(video_result.output_path),
        model_used=model_used,
        strategy=assignment.end_frame_strategy,
        primary_photo_path=assignment.primary_photo_path,
        end_frame_photo_path=assignment.end_frame_photo_path,
        reference_paths=list(assignment.additional_reference_photo_paths),
        summary_path=str(summary_path),
    )


def run_slot_job(
    job_id: str,
    *,
    project_slug: str,
    template_id: str,
    slot_id: str,
    image_model: str | None = None,
    generative_video_model: str | None = None,
    video_duration: int = DEFAULT_VIDEO_DURATION_SEC,
    disable_end_frame: bool = False,
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

        def _progress(status: str, pct: int, msg: str, extra: dict | None) -> None:
            update_job(job_id, status=status, percent=pct, message=msg, **(extra or {}))

        result = _render_slot_pipeline(
            project_slug=project_slug,
            template_id=template_id,
            slot_id=slot_id,
            plan=plan,
            template=template,
            image_model=image_model,
            generative_video_model=generative_video_model,
            video_duration=video_duration,
            disable_end_frame=disable_end_frame,
            progress=_progress,
        )

        update_job(
            job_id,
            status="done",
            percent=100,
            message="Done",
            summary_path=_to_url(result.summary_path),
            video_url=_to_url(result.video_clip_path),
            model_used=result.model_used,
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


# ─── Classify-only worker (fires on studio open, builds the cached plan) ────


def run_classify_job(
    job_id: str,
    *,
    project_slug: str,
    template_id: str,
) -> None:
    """Build (or reuse) the assignment plan for a project + template. Surface
    progress through update_job(). Used when the studio view opens so the
    plan is ready by the time the user clicks a slot.

    Cheap if the plan is already cached (returns instantly); otherwise this
    runs N + M Gemini Flash vision calls for N photos and M slots with
    candidates. Costs cents.
    """
    try:
        update_job(
            job_id,
            status="classifying",
            percent=10,
            message="Building assignment plan",
        )
        plan = _get_or_build_plan(
            project_slug=project_slug,
            template_id=template_id,
        )
        active = sum(1 for a in plan.slot_assignments if a.is_active)
        update_job(
            job_id,
            status="done",
            percent=100,
            message=f"Plan ready · {active}/{len(plan.slot_assignments)} active slots",
        )
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[job {job_id}] CLASSIFY ERROR: {exc}\n{tb}")
        update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            message="Classification failed",
        )


def list_slot_results(project_slug: str, template_id: str) -> dict[str, JobRecord]:
    """Most-recent successful slot job per slot for a (project, template).

    Used by the UI on studio open to repopulate the slot strip with previously
    rendered videos. We scan outputs/jobs/*.json — slow if the user has
    thousands of jobs, but at hackathon scale it's nothing.
    """
    if not JOBS_DIR.exists():
        return {}
    by_slot: dict[str, JobRecord] = {}
    for path in JOBS_DIR.glob("*.json"):
        try:
            job = JobRecord.model_validate_json(path.read_text())
        except Exception:
            continue
        if (
            job.kind != "run-slot"
            or job.project_slug != project_slug
            or job.template_id != template_id
            or job.slot_id is None
            or job.status != "done"
            or not job.video_url
        ):
            continue
        prior = by_slot.get(job.slot_id)
        if prior is None or (job.finished_at or "") > (prior.finished_at or ""):
            by_slot[job.slot_id] = job
    return by_slot


def invalidate_plan_cache(project_slug: str) -> None:
    """Drop the cached classification plan for a project. Called when the
    photo set changes (upload, delete) so the next classify run sees fresh
    data."""
    cache_path = _plan_cache_path(project_slug)
    if cache_path.exists():
        try:
            cache_path.unlink()
        except OSError:
            pass


# ─── Audio + exports support ────────────────────────────────────────────────


SUPPORTED_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def _audio_track_path(filename: str) -> Path | None:
    """Resolve a saved track filename to a real path under audio/. Returns
    None if the file is missing or escapes the audio root (defensive)."""
    if not filename:
        return None
    safe = Path(filename).name  # strip any path components
    p = (AUDIO_ROOT / safe).resolve()
    try:
        p.relative_to(AUDIO_ROOT.resolve())
    except ValueError:
        return None
    if not p.exists() or not p.is_file():
        return None
    return p


def list_audio_tracks() -> list[dict]:
    """Every audio file currently in audio/. Exposed via /audio/tracks."""
    if not AUDIO_ROOT.exists():
        return []
    out: list[dict] = []
    for f in sorted(AUDIO_ROOT.iterdir()):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            continue
        out.append({
            "filename": f.name,
            "url": f"/audio/{f.name}",
            "size_bytes": f.stat().st_size,
        })
    return out


def _probe_total_duration(video_paths: list[Path]) -> float:
    """Sum the duration of every clip via ffprobe. Used to pin the export
    output to an exact length with `-t`, rather than relying on `-shortest`
    which rounds DOWN to the nearest AAC frame boundary and produces an
    audio stream metadata duration ~80ms shorter than the video. macOS
    native players (QuickTime, Finder QuickLook) treat that mismatch as a
    malformed audio track and silently drop it.
    """
    total = 0.0
    for p in video_paths:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(p),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed on {p}: {proc.stderr}")
        try:
            total += float(proc.stdout.strip())
        except ValueError:
            raise RuntimeError(f"ffprobe returned non-numeric duration for {p}: {proc.stdout!r}")
    return total


def _ffmpeg_concat_with_optional_audio(
    *,
    slot_video_paths: list[Path],
    audio_path: Path | None,
    output_path: Path,
) -> None:
    """Concat slot mp4s losslessly via ffmpeg's concat demuxer.

    Audio policy: ANY audio baked into the slot videos is stripped. Some
    video models (Veo 3 with audio enabled, Seedance variants, etc.) may
    bake in their own audio — we always discard it and either replace
    with the user-selected music track or leave the export silent.

    • audio_path is None → silent export. We explicitly map only the
      video stream from input 0 (`-map 0:v:0`) and drop everything else
      with `-an`.
    • audio_path is set → mux the music on top, looped via
      `-stream_loop -1` and trimmed with `-shortest` so the audio
      matches the video duration regardless of track length.

    Hackathon-grade: assumes all slot videos share resolution / fps /
    codec, which they do because they all come from Kling 3.0 or
    DepthFlow rendered to the same template settings.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.parent / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in slot_video_paths)
    )

    if audio_path is None:
        # Silent export. Take only the video stream from the concat;
        # `-an` ensures no audio stream sneaks through even if some
        # slot clips have baked-in audio while others don't.
        # `-movflags +faststart` moves the moov atom to the front so
        # macOS native players (QuickTime, Finder QuickLook) can read
        # the file as a stream instead of needing the full body first.
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        # User picked a track. Multi-step audio handling to make the
        # output play correctly EVERYWHERE (not just browsers):
        #
        #   1. Pre-probe total video duration so we can pin the output
        #      with -t exactly. -shortest rounds DOWN to the last full
        #      AAC frame, leaving audio metadata ~80ms shorter than
        #      video. macOS native players treat that as a malformed
        #      audio track and silently drop it (Chrome is forgiving).
        #   2. -stream_loop -1 — loop the audio source so we have
        #      enough material no matter how short the music is.
        #   3. apad — pad with silence after the loop so trimming with
        #      -t never falls in a gap.
        #   4. -t <duration> — force exact output length on every
        #      stream, so the audio tkhd matches mvhd exactly.
        #   5. -ar 48000 -ac 2 -profile:a aac_low — universal AAC-LC
        #      48 kHz stereo, the de facto mp4-video standard.
        #   6. -movflags +faststart — moov atom at the front so the
        #      file is readable as a stream by all players.
        total_duration = _probe_total_duration(slot_video_paths)
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-stream_loop", "-1",
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-profile:a", "aac_low",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-af", "apad",
            "-t", f"{total_duration:.3f}",
            "-movflags", "+faststart",
            str(output_path),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg concat failed (exit {proc.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {proc.stderr[-2000:]}"
        )


def _ffmpeg_extract_thumbnail(*, source_video: Path, output_image: Path) -> None:
    """Grab a single frame ~1s into the video for use as the export poster."""
    output_image.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", "00:00:01",
        "-i", str(source_video),
        "-frames:v", "1",
        "-q:v", "3",
        str(output_image),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Non-fatal: log but don't blow up the export.
        print(f"[thumbnail] failed: {proc.stderr[-500:]}")


def _persist_inline_slot_render(
    *,
    project_slug: str,
    template_id: str,
    slot_id: str,
    started_at: str,
    result: _SlotRenderResult,
) -> None:
    """Write a synthesized JobRecord with kind="run-slot" and status="done"
    for a slot that was rendered inline by run_template_job.

    Without this, only slots the user explicitly clicked through the slot
    strip would get a per-slot job file under outputs/jobs/. Slots rendered
    as part of a walkthrough export would have their video on disk but no
    JobRecord pointing at it, so the next walkthrough export would
    re-render them from scratch — exactly the bug Aaron observed where
    Export Video re-ran slots 7-12 even though they had completed in the
    prior template run.

    By persisting a per-slot record here, list_slot_results() finds these
    on subsequent exports and reuses the videos."""
    record = JobRecord(
        id=uuid.uuid4().hex,
        kind="run-slot",
        project_slug=project_slug,
        template_id=template_id,
        slot_id=slot_id,
        status="done",
        percent=100,
        message="Rendered inline as part of walkthrough export",
        started_at=started_at,
        finished_at=_now_iso(),
        run_dir=result.out_dir,
        summary_path=_to_url(result.summary_path),
        start_frame_url=_to_url(result.start_frame_path),
        end_frame_url=_to_url(result.end_frame_path) if result.end_frame_path else None,
        video_url=_to_url(result.video_clip_path),
        primary_photo_path=result.primary_photo_path,
        end_frame_photo_path=result.end_frame_photo_path,
        reference_paths=list(result.reference_paths),
        strategy=result.strategy,
        model_used=result.model_used,
    )
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _job_path(record.id).write_text(record.model_dump_json(indent=2))


def run_template_job(
    job_id: str,
    *,
    project_slug: str,
    template_id: str,
    audio_track: str | None = None,
    image_model: str | None = None,
    generative_video_model: str | None = None,
    video_duration: int = DEFAULT_VIDEO_DURATION_SEC,
) -> None:
    """Render every active slot in template order and concat the results
    into one mp4 under outputs/<project>/exports/<run_id>/walkthrough.mp4.

    Reuses any previously-rendered slot videos (via list_slot_results) so
    repeated exports are cheap; only missing slots run through the image →
    end-frame → video pipeline. If `audio_track` matches a file in audio/,
    it's mux'd onto the final concat (looped + -shortest)."""
    try:
        update_job(
            job_id,
            status="classifying",
            percent=2,
            message="Loading plan and template",
        )
        plan = _get_or_build_plan(
            project_slug=project_slug,
            template_id=template_id,
        )
        template_path = _resolve_template_path(template_id)
        template = load_template(template_path)

        # Active slots in render order — the template order field. Inactive
        # slots (no photo for that scene_kind) are skipped silently rather
        # than failing the whole export.
        all_slots = sorted(template.slots, key=lambda s: s.order)
        active_assignments = {
            a.slot_id: a for a in plan.slot_assignments if a.is_active
        }
        active_slots = [s for s in all_slots if s.id in active_assignments]
        if not active_slots:
            raise RuntimeError(
                "Template has no active slots — every slot was marked inactive "
                "during classification (did the project have any photos?)"
            )

        # Reuse previously-rendered slot videos; track which slots are missing.
        prior = list_slot_results(project_slug, template_id)
        slot_video_urls: list[str] = []
        slot_video_paths: list[Path] = []

        # Each missing slot gets ~80% of the progress bar split evenly across
        # them; the final 20% covers concat + audio mux.
        missing = [s for s in active_slots if s.id not in prior]
        per_slot_pct = (80 // max(len(missing), 1))
        base_pct = 5

        for idx, slot in enumerate(active_slots):
            existing = prior.get(slot.id)
            if existing and existing.video_url:
                # Reuse — convert the /outputs/... URL back to absolute path.
                rel = existing.video_url.removeprefix("/outputs/")
                slot_video_paths.append(OUTPUTS_ROOT / rel)
                slot_video_urls.append(existing.video_url)
                continue

            update_job(
                job_id,
                status="image_pass",
                percent=base_pct,
                message=f"Rendering slot {idx + 1}/{len(active_slots)}: {slot.label}",
                slot_id=slot.id,
            )

            def _progress(status: str, pct: int, msg: str, extra: dict | None) -> None:
                # Map per-slot 0..100 → our partial slice of the overall bar.
                slot_pct = base_pct + (pct * per_slot_pct // 100)
                update_job(
                    job_id,
                    status=status,
                    percent=min(slot_pct, 84),
                    message=f"Slot {idx + 1}/{len(active_slots)} · {msg}",
                    **(extra or {}),
                )

            slot_started_at = _now_iso()
            result = _render_slot_pipeline(
                project_slug=project_slug,
                template_id=template_id,
                slot_id=slot.id,
                plan=plan,
                template=template,
                image_model=image_model,
                generative_video_model=generative_video_model,
                video_duration=video_duration,
                progress=_progress,
            )
            slot_video_paths.append(Path(result.video_clip_path))
            url = _to_url(result.video_clip_path)
            if url:
                slot_video_urls.append(url)

            # Persist this inline render as a discoverable run-slot JobRecord
            # so the NEXT walkthrough export reuses it instead of re-running.
            _persist_inline_slot_render(
                project_slug=project_slug,
                template_id=template_id,
                slot_id=slot.id,
                started_at=slot_started_at,
                result=result,
            )
            base_pct += per_slot_pct

        # ─── Concat ───────────────────────────────────────────────────
        update_job(
            job_id,
            status="concatenating",
            percent=85,
            message=f"Stitching {len(slot_video_paths)} clips",
            slot_video_urls=slot_video_urls,
            slot_id=None,  # clear: we're past per-slot work
        )

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        export_id = f"{ts}_{uuid.uuid4().hex[:6]}"
        export_dir = OUTPUTS_ROOT / project_slug / "exports" / export_id
        export_dir.mkdir(parents=True, exist_ok=True)
        export_video = export_dir / "walkthrough.mp4"

        audio_path: Path | None = None
        if audio_track:
            audio_path = _audio_track_path(audio_track)
            if audio_path is None:
                raise RuntimeError(
                    f"Audio track '{audio_track}' not found in audio/. "
                    "Upload it via the Audio tab or remove the selection."
                )
            update_job(
                job_id,
                status="muxing_audio",
                percent=90,
                message=f"Muxing audio: {audio_track}",
                audio_track=audio_track,
            )

        _ffmpeg_concat_with_optional_audio(
            slot_video_paths=slot_video_paths,
            audio_path=audio_path,
            output_path=export_video,
        )

        # ─── Thumbnail ────────────────────────────────────────────────
        thumb_path = export_dir / "thumbnail.jpg"
        _ffmpeg_extract_thumbnail(
            source_video=export_video,
            output_image=thumb_path,
        )

        # ─── Manifest write (for /projects/{slug}/exports listing) ────
        manifest = {
            "export_id": export_id,
            "project_slug": project_slug,
            "template_id": template_id,
            "audio_track": audio_track,
            "slot_count": len(slot_video_paths),
            "video_url": _to_url(export_video),
            "thumbnail_url": _to_url(thumb_path) if thumb_path.exists() else None,
            "finished_at": _now_iso(),
            "slot_video_urls": slot_video_urls,
        }
        (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        update_job(
            job_id,
            status="done",
            percent=100,
            message=f"Walkthrough ready · {len(slot_video_paths)} clips",
            export_video_url=_to_url(export_video),
            export_thumbnail_url=_to_url(thumb_path) if thumb_path.exists() else None,
            video_url=_to_url(export_video),  # mirrored so generic players can pick it up
            audio_track=audio_track,
            slot_video_urls=slot_video_urls,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[job {job_id}] TEMPLATE ERROR: {exc}\n{tb}")
        update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            message="Walkthrough failed",
        )


def list_exports(project_slug: str, template_id: str | None = None) -> list[dict]:
    """All completed walkthrough exports for a project, newest first.

    Reads the manifest.json each export job writes; the UI surfaces these
    in the Exports tab as clickable thumbnails. Falls back to scanning the
    jobs/ directory if a manifest is missing (older jobs).
    """
    exports_dir = OUTPUTS_ROOT / project_slug / "exports"
    if not exports_dir.exists():
        return []
    out: list[dict] = []
    for run_dir in sorted(exports_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        if template_id and manifest.get("template_id") != template_id:
            continue
        out.append(manifest)
    return out


def get_cached_plan(project_slug: str, template_id: str) -> dict | None:
    """Return the cached plan dict if it exists and matches the current
    photo set + template, else None."""
    project_dir = _resolve_project_dir(project_slug)
    photos = _project_photos(project_dir)
    if not photos:
        return None
    photo_hash = _photos_hash(photos)
    cache_path = _plan_cache_path(project_slug)
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text())
        if (
            cached.get("_photos_hash") == photo_hash
            and cached.get("_template_id") == template_id
        ):
            return cached.get("plan")
    except Exception:
        return None
    return None
