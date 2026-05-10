"""FastAPI backend for the Studio UI.

Exposes the i2v pipeline as HTTP endpoints the Next.js app consumes:

  GET  /health                     — sanity ping
  GET  /projects                   — list houses (subfolders of inputs/)
  GET  /projects/{slug}/photos     — list photos in a project
  GET  /templates                  — list templates
  GET  /templates/{id}             — full template JSON
  GET  /inputs/<...>               — static photo files
  GET  /outputs/<...>              — static rendered files (videos, frames)

The render-trigger endpoints (/jobs/classify, /jobs/run-slot) come next once
the UI's structural shape is locked. For now the UI consumes static metadata
and triggers nothing — that lets us iterate on layout without burning fal
credits during development.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.jobs import (
    AUDIO_ROOT,
    DEFAULT_GENERATIVE_VIDEO_MODEL,
    DEFAULT_VIDEO_DURATION_SEC,
    SUPPORTED_AUDIO_EXTS,
    JobRecord,
    create_job,
    get_cached_plan,
    get_job,
    invalidate_plan_cache,
    list_audio_tracks,
    list_exports,
    list_slot_results,
    run_classify_job,
    run_slot_job,
    run_template_job,
)
from i2v.models import list_known_models
from i2v.video_models import list_known_video_models


# ─── Paths ───────────────────────────────────────────────────────────────────
# Resolve relative to repo root, regardless of where uvicorn is launched from.
REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_ROOT = REPO_ROOT / "inputs"
OUTPUTS_ROOT = REPO_ROOT / "outputs"
TEMPLATES_ROOT = REPO_ROOT / "templates"
# AUDIO_ROOT comes from api.jobs so the worker and the static mount agree on
# a single source of truth.

SUPPORTED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


# ─── Helpers ────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "house"


def _project_dirs() -> list[Path]:
    """Subdirectories of inputs/ that contain at least one supported photo."""
    if not INPUTS_ROOT.exists():
        return []
    out: list[Path] = []
    for p in sorted(INPUTS_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if any(
            f.is_file() and f.suffix.lower() in SUPPORTED_PHOTO_EXTS
            for f in p.iterdir()
        ):
            out.append(p)
    return out


def _resolve_project(slug: str) -> Path:
    """Find a project dir by slug, including empty ones. 404 if not found.

    Does NOT require photos to be present — a freshly-created project
    folder (POST /projects, before photos have been uploaded) needs to be
    addressable so the subsequent upload call can target it. The gallery
    list endpoint still uses _project_dirs() which filters on photo
    presence, so empty placeholders don't appear in the UI until the
    upload populates them.
    """
    if not INPUTS_ROOT.exists():
        raise HTTPException(status_code=404, detail="inputs/ root missing")
    for p in sorted(INPUTS_ROOT.iterdir()):
        if p.is_dir() and _slugify(p.name) == slug:
            return p
    raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")


def _load_template(template_path: Path) -> dict:
    return json.loads(template_path.read_text())


# ─── Project metadata (template association, cover photo) ───────────────────


DEFAULT_PROJECT_TEMPLATE_ID = "cinematic-editorial-v1"


def _project_template_id(project_dir: Path) -> str:
    """Read which template a project was created with.

    Looks for inputs/<project>/meta.json with shape {"template_id": "..."}.
    Falls back to the default template (cinematic-editorial-v1) for projects
    that pre-date the meta.json convention.
    """
    meta_path = project_dir / "meta.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text())
            tid = data.get("template_id")
            if isinstance(tid, str) and tid:
                return tid
        except Exception:
            pass
    return DEFAULT_PROJECT_TEMPLATE_ID


def _project_template_name(template_id: str) -> str:
    """Best-effort lookup of a template's display name from its id."""
    if not TEMPLATES_ROOT.exists():
        return template_id
    for path in TEMPLATES_ROOT.glob("*.json"):
        try:
            t = _load_template(path)
        except Exception:
            continue
        meta = t.get("template", {})
        if meta.get("id") == template_id or path.stem == template_id:
            return meta.get("name", template_id)
    return template_id


def _project_cover_url(project_dir: Path) -> str | None:
    """Return a URL (relative to backend root) of the first photo in the project,
    used as the cover thumbnail in the Projects section."""
    photos = [
        f for f in sorted(project_dir.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_PHOTO_EXTS
    ]
    if not photos:
        return None
    return f"/inputs/{project_dir.name}/{photos[0].name}"


# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="i2v_templates studio API", version="0.0.1")

# Local-dev CORS. Locked down to localhost:3000 (Next.js dev server).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static mounts so the UI can reference photo/video URLs directly. We don't
# bother with auth at hackathon scale — local dev only.
if INPUTS_ROOT.exists():
    app.mount("/inputs", StaticFiles(directory=str(INPUTS_ROOT)), name="inputs")
if OUTPUTS_ROOT.exists():
    app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_ROOT)), name="outputs")
# Make sure audio/ exists before mounting — uvicorn errors out if the dir is
# missing at startup, and we want the studio to boot even on a fresh checkout.
AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(AUDIO_ROOT)), name="audio")


# ─── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "repo_root": str(REPO_ROOT),
        "projects_count": len(_project_dirs()),
        "templates_count": len(list(TEMPLATES_ROOT.glob("*.json"))) if TEMPLATES_ROOT.exists() else 0,
    }


@app.get("/projects")
def list_projects() -> list[dict]:
    """List houses (subfolders of inputs/) the user has uploaded.

    Each project carries the id and display name of the template it was
    created with, plus a cover photo URL the UI uses as the project tile
    thumbnail.
    """
    out = []
    for p in _project_dirs():
        photos = [
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_PHOTO_EXTS
        ]
        template_id = _project_template_id(p)
        out.append({
            "name": p.name,
            "slug": _slugify(p.name),
            "photo_count": len(photos),
            "template_id": template_id,
            "template_name": _project_template_name(template_id),
            "cover_photo_url": _project_cover_url(p),
        })
    return out


@app.get("/projects/{slug}/photos")
def list_project_photos(slug: str) -> list[dict]:
    """List photos in a project. Each photo includes a URL the UI can fetch."""
    project_dir = _resolve_project(slug)
    out = []
    for f in sorted(project_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_PHOTO_EXTS:
            continue
        # URL relative to /inputs static mount.
        url_path = f"/inputs/{project_dir.name}/{f.name}"
        out.append({
            "name": f.name,
            "url": url_path,
            "size_bytes": f.stat().st_size,
        })
    return out


@app.get("/templates")
def list_templates() -> list[dict]:
    """Summary of every template under templates/. Returns lightweight metadata
    only — fetch /templates/{id} for the full JSON."""
    if not TEMPLATES_ROOT.exists():
        return []
    out = []
    for path in sorted(TEMPLATES_ROOT.glob("*.json")):
        try:
            t = _load_template(path)
        except Exception:
            continue  # skip malformed templates rather than 500
        meta = t.get("template", {})
        out.append({
            "id": meta.get("id", path.stem),
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "thumbnail": meta.get("thumbnail"),
            "enabled": bool(meta.get("enabled", True)),
            "duration_sec": meta.get("duration_sec", 0),
            "slot_count": len(t.get("slots", [])),
            "filename": path.name,
        })
    # Surface the enabled template first.
    out.sort(key=lambda x: (not x["enabled"], x["name"]))
    return out


@app.get("/templates/{template_id}")
def get_template(template_id: str) -> dict:
    """Full template JSON, including all slot definitions."""
    if not TEMPLATES_ROOT.exists():
        raise HTTPException(status_code=404, detail="No templates dir")
    # Match by template.id field (canonical) OR by filename stem.
    for path in TEMPLATES_ROOT.glob("*.json"):
        try:
            t = _load_template(path)
        except Exception:
            continue
        if t.get("template", {}).get("id") == template_id or path.stem == template_id:
            return t
    raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")


# ─── Jobs ───────────────────────────────────────────────────────────────────


class RunSlotRequest(BaseModel):
    project_slug: str
    template_id: str
    slot_id: str
    # Override the per-slot template-defined image model for every pass in
    # the image pipeline. None = use whatever the template says per pass.
    image_model: str | None = None
    # Override the slot's video model. None = use template default
    # (DepthFlow preset for most slots; Kling 3.0 when an end frame is
    # synthesized). Set to a registered video model id to force a
    # specific model for ALL slots regardless of strategy.
    generative_video_model: str | None = None
    video_duration: int | None = None
    # When true, skip end-frame synthesis entirely and send only the start
    # frame to the video model. Useful when start↔end interpolation produces
    # artifacts on a particular slot.
    disable_end_frame: bool = False


@app.post("/jobs/run-slot")
def jobs_run_slot(req: RunSlotRequest, background: BackgroundTasks) -> JobRecord:
    """Start a single-slot generation. Returns the freshly-created job
    record; poll GET /jobs/{id} to follow progress.

    Validates the project exists and the slot is non-empty, but defers all
    other checks (slot inactivity, missing photos, etc.) to the worker so
    they surface as job errors rather than HTTP errors.
    """
    # Surface "project not found" as 400 immediately instead of letting it
    # blow up inside the worker — easier debugging.
    try:
        _resolve_project(req.project_slug)
    except HTTPException:
        raise

    job = create_job(
        project_slug=req.project_slug,
        template_id=req.template_id,
        slot_id=req.slot_id,
        kind="run-slot",
    )
    background.add_task(
        run_slot_job,
        job.id,
        project_slug=req.project_slug,
        template_id=req.template_id,
        slot_id=req.slot_id,
        image_model=req.image_model,
        generative_video_model=req.generative_video_model,
        video_duration=req.video_duration or DEFAULT_VIDEO_DURATION_SEC,
        disable_end_frame=req.disable_end_frame,
    )
    return job


@app.get("/jobs/{job_id}")
def jobs_get(job_id: str) -> JobRecord:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


class ClassifyRequest(BaseModel):
    project_slug: str
    template_id: str


@app.post("/jobs/classify")
def jobs_classify(req: ClassifyRequest, background: BackgroundTasks) -> JobRecord:
    """Classify all photos in a project against a template's slot list, build
    the assignment plan, and cache it. Fired on studio open so the plan is
    ready by the time the user clicks any slot.

    Idempotent: if the cached plan already matches the current photo set
    and template, returns a job that completes instantly.
    """
    try:
        _resolve_project(req.project_slug)
    except HTTPException:
        raise

    job = create_job(
        project_slug=req.project_slug,
        template_id=req.template_id,
        slot_id=None,
        kind="classify",
    )
    background.add_task(
        run_classify_job,
        job.id,
        project_slug=req.project_slug,
        template_id=req.template_id,
    )
    return job


@app.get("/projects/{slug}/plan")
def get_project_plan(slug: str, template_id: str) -> dict:
    """Return the cached AssignmentPlan for (project, template) if one
    exists. 404 if no plan has been built yet (the UI uses this to decide
    whether to fire a classify job).
    """
    _resolve_project(slug)  # 404 if missing project
    plan = get_cached_plan(slug, template_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"No cached plan for project '{slug}' + template '{template_id}'",
        )
    return plan


@app.get("/projects/{slug}/slot-results")
def get_project_slot_results(slug: str, template_id: str) -> dict[str, JobRecord]:
    """Most-recent successful run per slot for a (project, template).
    The UI seeds its slotJobs state from this on studio open so previously
    rendered videos persist across navigations.
    """
    _resolve_project(slug)
    return list_slot_results(slug, template_id)


class CreateProjectRequest(BaseModel):
    name: str
    template_id: str | None = None


@app.post("/projects")
def create_project(req: CreateProjectRequest) -> dict:
    """Create a new project (a subfolder of inputs/) so the UI can name a
    fresh project and upload photos into it. Sanitizes the name into a
    folder-safe form, creates the directory, and (optionally) writes a
    meta.json pinning the template id.

    Returns the project's name + slug + an empty photos list.
    """
    raw_name = req.name.strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="Project name is required")
    # Folder name: keep alphanumerics + spaces -> underscores. The slug we
    # return is the URL-safe form (used everywhere else in the API).
    folder_name = re.sub(r"[^A-Za-z0-9 _.-]", "", raw_name).strip().replace(" ", "_") or "project"
    target = INPUTS_ROOT / folder_name
    if target.exists():
        # If a project by this name exists, just surface it — don't error.
        # User can decide whether to keep that name or rename and retry.
        pass
    else:
        target.mkdir(parents=True, exist_ok=True)
    if req.template_id:
        try:
            (target / "meta.json").write_text(
                json.dumps({"template_id": req.template_id}, indent=2)
            )
        except OSError:
            pass
    return {
        "name": target.name,
        "slug": _slugify(target.name),
        "photo_count": 0,
        "template_id": req.template_id or DEFAULT_PROJECT_TEMPLATE_ID,
        "template_name": _project_template_name(req.template_id or DEFAULT_PROJECT_TEMPLATE_ID),
        "cover_photo_url": None,
    }


# ─── Model registries ───────────────────────────────────────────────────────


@app.get("/models")
def get_models() -> dict:
    """Both registries (image + video models) so the studio can populate
    its model-picker dropdowns. Image and video are returned in a single
    response — the UI typically needs both at once.

    Each entry carries the canonical fal id, a human-readable label,
    free-form notes, and (for video) backend + duration constraints so
    the UI can disable picks that won't work for a given slot.
    """
    image_models = [
        {
            "id": m.id,
            "label": m.label,
            "notes": m.notes,
            "default_for": list(m.default_for),
        }
        for m in list_known_models()
    ]
    video_models = [
        {
            "id": m.id,
            "label": m.label,
            "notes": m.notes,
            "backend": m.backend,
            "supports_end_frame": m.supports_end_frame,
            "min_duration": m.min_duration,
            "max_duration": m.max_duration,
            "allowed_durations": list(m.allowed_durations) if m.allowed_durations else None,
            "cost_per_sec": m.cost_per_sec,
        }
        for m in list_known_video_models()
    ]
    return {
        "image_models": image_models,
        "video_models": video_models,
        "defaults": {
            "generative_video_model": DEFAULT_GENERATIVE_VIDEO_MODEL,
            "video_duration_sec": DEFAULT_VIDEO_DURATION_SEC,
        },
    }


# ─── Audio + walkthrough exports ─────────────────────────────────────────────


class RunTemplateRequest(BaseModel):
    project_slug: str
    template_id: str
    audio_track: str | None = None  # filename under audio/, or null
    image_model: str | None = None
    generative_video_model: str | None = None
    video_duration: int | None = None


@app.post("/jobs/run-template")
def jobs_run_template(req: RunTemplateRequest, background: BackgroundTasks) -> JobRecord:
    """Render every active slot in template order, concat the results, and
    optionally mux a music track on top. The frontend polls GET /jobs/{id}
    just like a slot job; on completion the export shows up under
    GET /projects/{slug}/exports.
    """
    try:
        _resolve_project(req.project_slug)
    except HTTPException:
        raise

    job = create_job(
        project_slug=req.project_slug,
        template_id=req.template_id,
        slot_id=None,
        kind="run-template",
    )
    background.add_task(
        run_template_job,
        job.id,
        project_slug=req.project_slug,
        template_id=req.template_id,
        audio_track=req.audio_track,
        image_model=req.image_model,
        generative_video_model=req.generative_video_model,
        video_duration=req.video_duration or DEFAULT_VIDEO_DURATION_SEC,
    )
    return job


@app.get("/audio-tracks")
def audio_tracks() -> list[dict]:
    """List all audio files in audio/. Frontend renders these as the audio
    track dropdown.

    Note: this lives at /audio-tracks (hyphenated) rather than /audio/tracks
    because the StaticFiles mount at /audio/ claims every GET under that
    prefix and would 404 on a missing 'tracks' file. POST /audio still
    works because StaticFiles only routes GET.
    """
    return list_audio_tracks()


@app.post("/audio")
async def upload_audio(files: list[UploadFile] = File(...)) -> dict:
    """Save uploaded audio files into audio/. Filename collisions get a
    numeric suffix so prior tracks aren't clobbered."""
    saved: list[dict] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_AUDIO_EXTS:
            continue
        safe_name = Path(f.filename).name
        dest = AUDIO_ROOT / safe_name
        n = 1
        while dest.exists():
            dest = AUDIO_ROOT / f"{Path(safe_name).stem}_{n}{ext}"
            n += 1
        contents = await f.read()
        dest.write_bytes(contents)
        saved.append({
            "filename": dest.name,
            "url": f"/audio/{dest.name}",
            "size_bytes": len(contents),
        })
    return {"saved": saved, "count": len(saved)}


@app.get("/projects/{slug}/exports")
def get_project_exports(slug: str, template_id: str | None = None) -> list[dict]:
    """Completed walkthrough exports for a project (newest first). Used by
    the Exports tab in the right panel.
    """
    _resolve_project(slug)
    return list_exports(slug, template_id=template_id)


@app.delete("/projects/{slug}")
def delete_project(slug: str) -> dict:
    """Permanently delete a project: removes inputs/<folder>, outputs/<slug>,
    and any per-slot job records that referenced this project. Plan cache
    is invalidated.

    Hackathon-grade: no soft-delete, no recycle bin. The frontend confirms
    with the user before calling this.
    """
    project_dir = _resolve_project(slug)
    project_outputs = OUTPUTS_ROOT / slug

    # 1. Remove the input dir (photos + meta.json).
    try:
        shutil.rmtree(project_dir)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove inputs/{project_dir.name}: {exc}",
        )

    # 2. Remove rendered outputs (slot videos, exports, frames, etc).
    if project_outputs.exists():
        try:
            shutil.rmtree(project_outputs)
        except OSError as exc:
            # Don't fail the whole delete if outputs cleanup partially fails;
            # the inputs are gone, so the project is functionally deleted.
            print(f"[delete] outputs cleanup failed: {exc}")

    # 3. Remove per-slot JobRecord files that reference this project so the
    #    studio doesn't try to surface stale renders if the user re-creates
    #    a project with the same slug later.
    from api.jobs import JOBS_DIR, invalidate_plan_cache, JobRecord
    if JOBS_DIR.exists():
        for path in JOBS_DIR.glob("*.json"):
            try:
                rec = JobRecord.model_validate_json(path.read_text())
            except Exception:
                continue
            if rec.project_slug == slug:
                try:
                    path.unlink()
                except OSError:
                    pass

    invalidate_plan_cache(slug)
    return {"deleted": slug, "ok": True}


@app.post("/projects/{slug}/photos")
async def upload_photos(slug: str, files: list[UploadFile] = File(...)) -> dict:
    """Save uploaded photos into inputs/<slug>/. Invalidates the cached
    classification plan because the photo set has changed; the UI's next
    classify call will re-run.
    """
    project_dir = _resolve_project(slug)
    saved: list[dict] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_PHOTO_EXTS:
            continue
        # Sanitize filename: take basename, prepend timestamp if needed.
        safe_name = Path(f.filename).name
        dest = project_dir / safe_name
        n = 1
        while dest.exists():
            dest = project_dir / f"{Path(safe_name).stem}_{n}{ext}"
            n += 1
        contents = await f.read()
        dest.write_bytes(contents)
        saved.append({
            "name": dest.name,
            "url": f"/inputs/{project_dir.name}/{dest.name}",
            "size_bytes": len(contents),
        })
    invalidate_plan_cache(slug)
    return {"saved": saved, "count": len(saved)}
