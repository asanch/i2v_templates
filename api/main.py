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
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.jobs import (
    DEFAULT_GENERATIVE_VIDEO_MODEL,
    DEFAULT_VIDEO_DURATION_SEC,
    JobRecord,
    create_job,
    get_job,
    run_slot_job,
)


# ─── Paths ───────────────────────────────────────────────────────────────────
# Resolve relative to repo root, regardless of where uvicorn is launched from.
REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_ROOT = REPO_ROOT / "inputs"
OUTPUTS_ROOT = REPO_ROOT / "outputs"
TEMPLATES_ROOT = REPO_ROOT / "templates"

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
    """Find a project dir by slug. 404 if not found."""
    for p in _project_dirs():
        if _slugify(p.name) == slug:
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
    generative_video_model: str | None = None  # default Kling 3.0 Pro per jobs.DEFAULT
    video_duration: int | None = None


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
        generative_video_model=(
            req.generative_video_model or DEFAULT_GENERATIVE_VIDEO_MODEL
        ),
        video_duration=req.video_duration or DEFAULT_VIDEO_DURATION_SEC,
    )
    return job


@app.get("/jobs/{job_id}")
def jobs_get(job_id: str) -> JobRecord:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job
