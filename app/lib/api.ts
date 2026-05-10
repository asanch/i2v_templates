/**
 * Typed client for the FastAPI backend (api/main.py).
 *
 * All calls go through BACKEND_URL, which the user sets in app/.env.local
 * via NEXT_PUBLIC_BACKEND_URL. Defaults to http://localhost:8000 for local
 * dev. The backend serves photo + video files via /inputs/ and /outputs/
 * static mounts; the URLs we get back from /projects/{slug}/photos can be
 * fetched directly with `${BACKEND_URL}${url}`.
 */

export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export type Project = {
  name: string;
  slug: string;
  photo_count: number;
  template_id: string;
  template_name: string;
  cover_photo_url: string | null;
};

export type Photo = {
  name: string;
  url: string; // relative path on the backend, e.g. /inputs/thatcher/IMG.jpg
  size_bytes: number;
};

export type TemplateSummary = {
  id: string;
  name: string;
  description: string;
  thumbnail: string | null;
  enabled: boolean;
  duration_sec: number;
  slot_count: number;
  filename: string;
};

export type SlotDefinition = {
  id: string;
  label: string;
  order: number;
  required: boolean;
  photo_requirement: {
    scene_kind: string;
    intent: string;
    composition_hints?: string[];
    ideal_time_of_day?: string;
  };
  image_pipeline: Array<{
    label: string;
    source: string;
    model: string;
    prompt: string;
    parameters: Record<string, unknown>;
  }>;
  video_pass?: {
    model: string;
    prompt: string;
    duration_sec: number;
  };
};

export type TemplateFull = {
  schema_version: string;
  template: {
    id: string;
    name: string;
    description?: string;
    duration_sec: number;
    aspect_ratio: string;
    resolution: string;
    fps: number;
    thumbnail: string | null;
    enabled: boolean;
  };
  slots: SlotDefinition[];
};

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function fetchProjects(): Promise<Project[]> {
  return fetchJSON<Project[]>("/projects");
}

export async function fetchPhotos(slug: string): Promise<Photo[]> {
  return fetchJSON<Photo[]>(`/projects/${encodeURIComponent(slug)}/photos`);
}

export async function fetchTemplates(): Promise<TemplateSummary[]> {
  return fetchJSON<TemplateSummary[]>("/templates");
}

export async function fetchTemplate(id: string): Promise<TemplateFull> {
  return fetchJSON<TemplateFull>(`/templates/${encodeURIComponent(id)}`);
}

export function backendURL(path: string): string {
  // For converting a relative backend URL (e.g. /inputs/thatcher/IMG.jpg)
  // into a fully-qualified one for <img src> / <video src>.
  return `${BACKEND_URL}${path}`;
}


// ─── Jobs ───────────────────────────────────────────────────────────────────


export type JobStatus =
  | "queued"
  | "classifying"
  | "image_pass"
  | "end_frame"
  | "video_pass"
  | "concatenating"
  | "muxing_audio"
  | "done"
  | "error";

export type JobRecord = {
  id: string;
  kind: "run-slot" | "run-template" | "classify";
  project_slug: string;
  template_id: string;
  slot_id: string | null;

  status: JobStatus;
  percent: number;
  message: string;
  error: string | null;

  started_at: string;
  finished_at: string | null;

  run_dir: string | null;
  summary_path: string | null;
  start_frame_url: string | null;
  end_frame_url: string | null;
  video_url: string | null;

  primary_photo_path: string | null;
  end_frame_photo_path: string | null;
  reference_paths: string[];
  strategy: string | null;
  model_used: string | null;

  // Template-export fields (populated for kind === "run-template")
  export_video_url: string | null;
  export_thumbnail_url: string | null;
  audio_track: string | null;
  slot_video_urls: string[];
};

export type RunSlotRequest = {
  project_slug: string;
  template_id: string;
  slot_id: string;
  generative_video_model?: string;
  video_duration?: number;
};

export async function runSlot(req: RunSlotRequest): Promise<JobRecord> {
  const res = await fetch(`${BACKEND_URL}/jobs/run-slot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /jobs/run-slot → ${res.status}: ${text}`);
  }
  return (await res.json()) as JobRecord;
}

export async function pollJob(jobId: string): Promise<JobRecord> {
  return fetchJSON<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);
}

export async function classifyProject(
  project_slug: string,
  template_id: string,
): Promise<JobRecord> {
  const res = await fetch(`${BACKEND_URL}/jobs/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_slug, template_id }),
  });
  if (!res.ok) {
    throw new Error(`POST /jobs/classify → ${res.status}`);
  }
  return (await res.json()) as JobRecord;
}

// Mirror of i2v.classifier.SlotAssignment (the parts the UI needs).
export type PlanSlotAssignment = {
  slot_id: string;
  is_active: boolean;
  inactive_reason: string | null;
  primary_photo_path: string | null;
  end_frame_strategy:
    | "real_reference"
    | "multi_ref_inpaint"
    | "depthflow_only"
    | "none";
  end_frame_photo_path: string | null;
  end_frame_match_confidence: number | null;
  additional_reference_photo_paths: string[];
};

export type ProjectPlan = {
  template_id: string;
  slot_assignments: PlanSlotAssignment[];
  inactive_slot_ids: string[];
  unassigned_photo_paths: string[];
};

export async function fetchProjectPlan(
  project_slug: string,
  template_id: string,
): Promise<ProjectPlan | null> {
  const res = await fetch(
    `${BACKEND_URL}/projects/${encodeURIComponent(project_slug)}/plan?template_id=${encodeURIComponent(template_id)}`,
    { cache: "no-store" },
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET plan → ${res.status}`);
  return (await res.json()) as ProjectPlan;
}

/** Most-recent successful slot job per slot. Used to seed UI state on
 *  studio open so rendered videos survive navigation. */
export async function fetchSlotResults(
  project_slug: string,
  template_id: string,
): Promise<Record<string, JobRecord>> {
  return fetchJSON<Record<string, JobRecord>>(
    `/projects/${encodeURIComponent(project_slug)}/slot-results?template_id=${encodeURIComponent(template_id)}`,
  );
}

/** Create a new project folder under inputs/. Used by the new-from-blank
 *  flow so the user can name a project, upload photos, and have it appear
 *  in the project tile gallery. */
export async function createProject(
  name: string,
  template_id?: string,
): Promise<Project> {
  const res = await fetch(`${BACKEND_URL}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, template_id }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /projects → ${res.status}: ${text}`);
  }
  return (await res.json()) as Project;
}

/** Multipart photo upload. Invalidates the project's cached plan on the
 *  backend, so the caller should re-fetch plan / re-classify after this
 *  resolves. */
export async function uploadPhotos(
  project_slug: string,
  files: File[],
): Promise<{ saved: Photo[]; count: number }> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const res = await fetch(
    `${BACKEND_URL}/projects/${encodeURIComponent(project_slug)}/photos`,
    { method: "POST", body: fd },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /projects/${project_slug}/photos → ${res.status}: ${text}`);
  }
  return await res.json();
}


// ─── Walkthrough exports + audio ────────────────────────────────────────────


export type AudioTrack = {
  filename: string;
  url: string; // /audio/<filename>
  size_bytes: number;
};

/** Manifest written by the run-template job for each completed export.
 *  The Exports tab renders these as clickable thumbnails. */
export type ExportManifest = {
  export_id: string;
  project_slug: string;
  template_id: string;
  audio_track: string | null;
  slot_count: number;
  video_url: string;            // /outputs/<project>/exports/<id>/walkthrough.mp4
  thumbnail_url: string | null; // /outputs/<project>/exports/<id>/thumbnail.jpg
  finished_at: string;
  slot_video_urls: string[];
};

export type RunTemplateRequest = {
  project_slug: string;
  template_id: string;
  audio_track?: string | null;
  generative_video_model?: string;
  video_duration?: number;
};

/** Kick off a full-walkthrough render. Returns a JobRecord with kind
 *  "run-template"; the UI polls /jobs/{id} just like a slot job. */
export async function runTemplate(req: RunTemplateRequest): Promise<JobRecord> {
  const res = await fetch(`${BACKEND_URL}/jobs/run-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /jobs/run-template → ${res.status}: ${text}`);
  }
  return (await res.json()) as JobRecord;
}

export async function fetchAudioTracks(): Promise<AudioTrack[]> {
  return fetchJSON<AudioTrack[]>("/audio/tracks");
}

export async function uploadAudioTracks(
  files: File[],
): Promise<{ saved: AudioTrack[]; count: number }> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const res = await fetch(`${BACKEND_URL}/audio`, { method: "POST", body: fd });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /audio → ${res.status}: ${text}`);
  }
  return await res.json();
}

export async function fetchExports(
  project_slug: string,
  template_id: string,
): Promise<ExportManifest[]> {
  return fetchJSON<ExportManifest[]>(
    `/projects/${encodeURIComponent(project_slug)}/exports?template_id=${encodeURIComponent(template_id)}`,
  );
}

