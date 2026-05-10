"use client";

import { useEffect, useRef, useState } from "react";

import {
  JobRecord,
  Photo,
  PlanSlotAssignment,
  ProjectPlan,
  SlotDefinition,
  TemplateFull,
  TemplateSummary,
  backendURL,
  classifyProject,
  createProject,
  fetchProjectPlan,
  fetchSlotResults,
  runSlot,
  uploadPhotos,
} from "@/lib/api";
import { useJob } from "@/lib/useJob";

type Props = {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  selectedProjectName: string | null;
  selectedProjectSlug: string | null;
  onBackToProjects: () => void;
  onPhotosChanged: () => void;
  onProjectCreated: (slug: string) => void;
};

/** What the center video/image area is currently displaying. */
type PlayerSource =
  | { kind: "slot"; slotId: string }
  | { kind: "image"; url: string; label: string }
  | null;

/**
 * Three-pane studio. No page scroll; only the photos panel scrolls
 * internally. The center stack pins player + slot strip + generate bar.
 *
 * Job state lives at this level: a `slotJobs` map from slot_id → job_id.
 * Each SlotThumb polls its own job via useJob, surfacing status on the
 * thumb. Clicking a slot starts a job (if none yet) AND selects it for
 * playback in the center video player.
 */
export default function StudioWorkspace({
  template,
  templateSummary,
  photos,
  selectedProjectName,
  selectedProjectSlug,
  onBackToProjects,
  onPhotosChanged,
  onProjectCreated,
}: Props) {
  const [slotJobs, setSlotJobs] = useState<Record<string, string>>({});
  // playerSource drives what's in the center video/image area: a slot's
  // job (in progress / done video), an image preview from the photos panel
  // or right-pane frames, or nothing.
  const [playerSource, setPlayerSource] = useState<PlayerSource>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // Convenience: which slot is "selected" for the slot-strip ring.
  const selectedSlotId =
    playerSource?.kind === "slot" ? playerSource.slotId : null;

  // Plan + classification job state. When the studio opens with a project,
  // we auto-fire a classify job (or use the cached plan if present) so the
  // slot strip can show real photo assignments before the user clicks.
  const [plan, setPlan] = useState<ProjectPlan | null>(null);
  const [classifyJobId, setClassifyJobId] = useState<string | null>(null);
  const { job: classifyJob } = useJob(classifyJobId);

  // On project / template change: reset state, seed slot jobs from any
  // previously-completed runs (so videos persist across navigations), try
  // to load cached plan, and kick off classify if no cache exists.
  useEffect(() => {
    setSlotJobs({});
    setPlayerSource(null);
    setStartError(null);
    setPlan(null);
    setClassifyJobId(null);

    if (!selectedProjectSlug || !templateSummary.enabled) return;
    let cancelled = false;
    (async () => {
      try {
        // 1. Seed slotJobs from previously-completed runs so the slot strip
        //    shows finished videos as soon as the studio mounts.
        const slotResults = await fetchSlotResults(
          selectedProjectSlug,
          template.template.id,
        );
        if (cancelled) return;
        const seed: Record<string, string> = {};
        for (const [slotId, job] of Object.entries(slotResults)) {
          seed[slotId] = job.id;
        }
        if (Object.keys(seed).length > 0) setSlotJobs(seed);

        // 2. Look up cached plan; if none, fire classification.
        const cached = await fetchProjectPlan(
          selectedProjectSlug,
          template.template.id,
        );
        if (cancelled) return;
        if (cached) {
          setPlan(cached);
          return;
        }
        const job = await classifyProject(
          selectedProjectSlug,
          template.template.id,
        );
        if (cancelled) return;
        setClassifyJobId(job.id);
      } catch (err) {
        if (!cancelled) setStartError(`Plan fetch failed: ${err}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedProjectSlug, template.template.id, templateSummary.enabled]);

  // When the classify job lands, re-fetch the plan.
  useEffect(() => {
    if (
      classifyJob?.status === "done" &&
      selectedProjectSlug &&
      templateSummary.enabled
    ) {
      fetchProjectPlan(selectedProjectSlug, template.template.id)
        .then((p) => p && setPlan(p))
        .catch(() => undefined);
    }
  }, [
    classifyJob?.status,
    selectedProjectSlug,
    template.template.id,
    templateSummary.enabled,
  ]);

  const assignmentBySlot: Record<string, PlanSlotAssignment> = {};
  if (plan) {
    for (const a of plan.slot_assignments) assignmentBySlot[a.slot_id] = a;
  }

  const isClassifying =
    classifyJob !== null &&
    classifyJob.status !== "done" &&
    classifyJob.status !== "error";

  /** Show a still image (from the photos panel or a frame thumbnail) in
   *  the center player. */
  const handlePreviewImage = (url: string, label: string) => {
    setPlayerSource({ kind: "image", url, label });
  };

  // Click handler: selects the slot for the player; if no job has been
  // kicked off for this slot yet, start one.
  const handleSlotClick = async (slot: SlotDefinition) => {
    setPlayerSource({ kind: "slot", slotId: slot.id });
    if (slotJobs[slot.id]) return; // already running or done

    if (!selectedProjectSlug) {
      setStartError(
        "Pick a project before generating — slots need photos to run."
      );
      return;
    }
    if (!templateSummary.enabled) {
      setStartError("This template is a stub; generation is disabled.");
      return;
    }
    // Don't fire a slot run while classification is still in flight — the
    // worker would just block on the plan lock and confuse the UI.
    if (isClassifying) {
      setStartError(
        "Classifying photos first — give it a few seconds and try again."
      );
      return;
    }

    try {
      setStartError(null);
      const job = await runSlot({
        project_slug: selectedProjectSlug,
        template_id: template.template.id,
        slot_id: slot.id,
      });
      setSlotJobs((prev) => ({ ...prev, [slot.id]: job.id }));
    } catch (err) {
      setStartError(String(err));
    }
  };

  const [uploading, setUploading] = useState(false);

  /**
   * Upload one or more photo files. Three flows:
   *   1. Existing project (selectedProjectSlug set) → upload + re-classify.
   *   2. New project (no slug) → prompt for a project name, create the
   *      folder, attach the current template, upload, then notify the
   *      page so the project switcher picks up the new slug.
   *   3. Empty selection → bail with a clear message.
   */
  const handleUploadFiles = async (files: FileList | File[]) => {
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;

    setUploading(true);
    setStartError(null);
    try {
      let slugToUse = selectedProjectSlug;
      // New-from-blank: prompt for a project name and create the folder.
      if (!slugToUse) {
        const suggested = "My Project";
        const name = window.prompt(
          "Name this project — used as the folder name under inputs/",
          suggested,
        );
        if (!name) {
          setUploading(false);
          return;
        }
        const created = await createProject(name, template.template.id);
        slugToUse = created.slug;
        // Surface the new project to the page so its project list refreshes
        // and selectedProjectSlug switches to it.
        onProjectCreated(slugToUse);
      }
      await uploadPhotos(slugToUse, fileArr);
      onPhotosChanged();
      const job = await classifyProject(slugToUse, template.template.id);
      setClassifyJobId(job.id);
      setPlan(null);
    } catch (err) {
      setStartError(`Upload failed: ${err}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <main className="grid min-h-0 flex-1 grid-cols-[minmax(220px,_280px)_minmax(0,_1fr)_minmax(220px,_280px)] gap-3 overflow-hidden bg-neutral-950 p-3">
      <PhotosPanel
        photos={photos}
        projectName={selectedProjectName}
        // Upload is allowed any time the template is enabled. With no
        // project selected, the handler prompts for a project name.
        canUpload={templateSummary.enabled}
        uploading={uploading}
        onUploadFiles={handleUploadFiles}
        onPreviewImage={handlePreviewImage}
      />
      <CenterPanel
        template={template}
        templateSummary={templateSummary}
        photos={photos}
        projectName={selectedProjectName}
        onBackToProjects={onBackToProjects}
        slotJobs={slotJobs}
        playerSource={playerSource}
        onSlotClick={handleSlotClick}
        startError={startError}
        assignmentBySlot={assignmentBySlot}
        classifyJob={classifyJob}
      />
      <RightPanel
        template={template}
        slotJobs={slotJobs}
        selectedSlotId={selectedSlotId}
        onPreviewImage={handlePreviewImage}
      />
    </main>
  );
}

/* ─── Photos panel ──────────────────────────────────────────────────────── */

function PhotosPanel({
  photos,
  projectName,
  canUpload,
  uploading,
  onUploadFiles,
  onPreviewImage,
}: {
  photos: Photo[];
  projectName: string | null;
  canUpload: boolean;
  uploading: boolean;
  onUploadFiles: (files: FileList | File[]) => void;
  onPreviewImage: (url: string, label: string) => void;
}) {
  // Hidden file input. The visible "+" tile triggers it via ref so the
  // user only ever interacts with the styled button.
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <aside className="flex min-h-0 flex-col rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-4 py-3">
        <div className="flex gap-1 rounded-lg bg-neutral-950 p-1 text-xs">
          <span className="rounded-md bg-neutral-800 px-3 py-1 text-neutral-100">Photos</span>
          <span className="px-3 py-1 text-neutral-500">Video</span>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto p-3">
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              onUploadFiles(e.target.files);
              e.target.value = "";
            }
          }}
        />
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => canUpload && !uploading && inputRef.current?.click()}
            disabled={!canUpload || uploading}
            className="flex aspect-square cursor-pointer flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed border-neutral-700 text-neutral-500 transition hover:border-neutral-500 hover:text-neutral-200 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-neutral-700 disabled:hover:text-neutral-500"
            title={
              !canUpload
                ? "Pick an existing project first (new-from-style upload coming soon)"
                : uploading
                  ? "Uploading…"
                  : "Add photos"
            }
          >
            {uploading ? (
              <>
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-600 border-t-neutral-300" />
                <span className="text-[10px]">Uploading…</span>
              </>
            ) : (
              <>
                <span className="text-2xl leading-none">+</span>
                <span className="text-[10px]">Add photos</span>
              </>
            )}
          </button>
          {photos.map((p) => (
            <button
              key={p.url}
              onClick={() => onPreviewImage(backendURL(p.url), p.name)}
              className="group aspect-square cursor-pointer overflow-hidden rounded-md bg-neutral-800 ring-1 ring-transparent transition hover:ring-blue-500"
              title={`${p.name} — click to preview`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={backendURL(p.url)}
                alt={p.name}
                className="h-full w-full object-cover transition group-hover:scale-[1.03]"
                loading="lazy"
              />
            </button>
          ))}
        </div>
        {photos.length === 0 && (
          <p className="mt-3 text-center text-xs text-neutral-500">
            {projectName ? (
              <>Click + to add the first photo to this project</>
            ) : (
              <>Pick an existing project or create the folder manually</>
            )}
          </p>
        )}
      </div>
    </aside>
  );
}

/* ─── Center panel ──────────────────────────────────────────────────────── */

function CenterPanel({
  template,
  templateSummary,
  photos,
  projectName,
  onBackToProjects,
  slotJobs,
  playerSource,
  onSlotClick,
  startError,
  assignmentBySlot,
  classifyJob,
}: {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  projectName: string | null;
  onBackToProjects: () => void;
  slotJobs: Record<string, string>;
  playerSource: PlayerSource;
  onSlotClick: (slot: SlotDefinition) => void;
  startError: string | null;
  assignmentBySlot: Record<string, PlanSlotAssignment>;
  classifyJob: JobRecord | null;
}) {
  const slots = template.slots;
  const enabled = templateSummary.enabled;
  const isNewProject = !projectName;
  const selectedSlotId =
    playerSource?.kind === "slot" ? playerSource.slotId : null;
  const selectedSlot = selectedSlotId
    ? slots.find((s) => s.id === selectedSlotId) ?? null
    : null;

  return (
    <section className="flex min-w-0 min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between gap-3 rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <button
            onClick={onBackToProjects}
            className="flex shrink-0 items-center gap-1 rounded-md border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-300 transition hover:border-neutral-500 hover:text-neutral-100"
            title="Back to all projects"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-3.5 w-3.5"
            >
              <path d="M19 12H5" />
              <path d="M12 19l-7-7 7-7" />
            </svg>
            All projects
          </button>
          <div className="min-w-0">
            <p className="truncate text-base font-semibold text-neutral-100">
              {projectName ?? "New Project"}
            </p>
            <p className="truncate text-xs text-neutral-500">
              {templateSummary.name} · {slots.length} slots ·{" "}
              {template.template.duration_sec}s · {template.template.aspect_ratio}
            </p>
          </div>
        </div>
        {isNewProject && (
          <span className="shrink-0 rounded-full bg-blue-950/50 px-3 py-1 text-xs font-medium text-blue-300 ring-1 ring-blue-900">
            New
          </span>
        )}
      </div>

      <PlayerArea
        playerSource={playerSource}
        selectedSlot={selectedSlot}
        selectedSlotJobId={selectedSlotId ? slotJobs[selectedSlotId] ?? null : null}
        photos={photos}
        isNewProject={isNewProject}
        slotsCount={slots.length}
        enabled={enabled}
        startError={startError}
      />

      {classifyJob && classifyJob.status !== "done" && (
        <div className="flex shrink-0 items-center gap-3 rounded-xl border border-blue-900/60 bg-blue-950/30 px-4 py-2 text-xs text-blue-200">
          {classifyJob.status === "error" ? (
            <span className="text-red-300">
              Classification failed: {classifyJob.error}
            </span>
          ) : (
            <>
              <div className="h-3 w-3 animate-spin rounded-full border-2 border-blue-500/30 border-t-blue-300" />
              <span>{classifyJob.message}</span>
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-blue-950">
                <div
                  className="h-full bg-blue-400 transition-[width]"
                  style={{ width: `${classifyJob.percent}%` }}
                />
              </div>
              <span className="font-mono text-[11px]">{classifyJob.percent}%</span>
            </>
          )}
        </div>
      )}

      <SlotStrip
        slots={slots}
        photos={photos}
        slotJobs={slotJobs}
        selectedSlotId={selectedSlotId}
        onSlotClick={onSlotClick}
        assignmentBySlot={assignmentBySlot}
      />

      <div className="flex shrink-0 items-center gap-3 rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-3">
        <button
          className="rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!enabled || photos.length === 0}
          title={
            !enabled
              ? "This template is a stub"
              : photos.length === 0
                ? "Add images to this project first"
                : "Run the full walkthrough (concat coming next)"
          }
        >
          {isNewProject || photos.length === 0
            ? "Add images first"
            : "Generate full walkthrough"}
        </button>
      </div>
    </section>
  );
}

/* ─── Player area ───────────────────────────────────────────────────────── */

function PlayerArea({
  playerSource,
  selectedSlot,
  selectedSlotJobId,
  photos,
  isNewProject,
  slotsCount,
  enabled,
  startError,
}: {
  playerSource: PlayerSource;
  selectedSlot: SlotDefinition | null;
  selectedSlotJobId: string | null;
  photos: Photo[];
  isNewProject: boolean;
  slotsCount: number;
  enabled: boolean;
  startError: string | null;
}) {
  const { job } = useJob(selectedSlotJobId);

  const isImagePreview = playerSource?.kind === "image";
  const hasVideo = !isImagePreview && job?.status === "done" && job.video_url;

  // Top-right badge label.
  const badge = isImagePreview
    ? "Image"
    : hasVideo
      ? "Rendered"
      : "Preview";

  return (
    <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-xl border border-neutral-800 bg-black">
      {isImagePreview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={playerSource.url}
          src={playerSource.url}
          alt={playerSource.label}
          className="h-full w-full object-contain"
        />
      ) : hasVideo ? (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video
          key={job!.video_url!}
          src={backendURL(job!.video_url!)}
          controls
          autoPlay
          loop
          className="h-full w-full object-contain"
        />
      ) : selectedSlot ? (
        <SlotJobOverlay slot={selectedSlot} job={job} photos={photos} />
      ) : (
        <PlayerEmptyState
          isNewProject={isNewProject}
          slotsCount={slotsCount}
          photoCount={photos.length}
          enabled={enabled}
          startError={startError}
        />
      )}
      <span className="absolute right-3 top-3 rounded-full bg-black/60 px-2 py-1 text-xs text-neutral-300 backdrop-blur-sm">
        {badge}
      </span>
      {isImagePreview && (
        <span className="absolute left-3 top-3 max-w-[60%] truncate rounded-full bg-black/60 px-2 py-1 text-xs text-neutral-300 backdrop-blur-sm">
          {playerSource.label}
        </span>
      )}
    </div>
  );
}

function PlayerEmptyState({
  isNewProject,
  slotsCount,
  photoCount,
  enabled,
  startError,
}: {
  isNewProject: boolean;
  slotsCount: number;
  photoCount: number;
  enabled: boolean;
  startError: string | null;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 text-center text-neutral-500">
      {isNewProject ? (
        <>
          <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-dashed border-neutral-700 text-3xl text-neutral-500">
            +
          </div>
          <p className="text-sm text-neutral-300">
            Add images to start this project
          </p>
          <p className="max-w-md text-xs">
            Drop photos in{" "}
            <code className="rounded bg-neutral-800 px-1 py-0.5 text-[11px]">
              inputs/&lt;your-project-name&gt;/
            </code>{" "}
            and refresh, or attach an existing project.
          </p>
        </>
      ) : photoCount === 0 ? (
        <p className="text-sm">No photos in this project yet.</p>
      ) : (
        <>
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-neutral-800">
            <svg viewBox="0 0 24 24" fill="currentColor" className="ml-1 h-7 w-7">
              <path d="M8 5v14l11-7L8 5z" />
            </svg>
          </div>
          <p className="text-sm">
            {enabled
              ? "Click any slot below to generate it"
              : "This template is a stub. Pick Cinematic Editorial to actually render."}
          </p>
          <p className="text-xs">
            {photoCount} photos available · {slotsCount} slots in template
          </p>
        </>
      )}
      {startError && (
        <p className="mt-2 max-w-md rounded border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300">
          {startError}
        </p>
      )}
    </div>
  );
}

function SlotJobOverlay({
  slot,
  job,
  photos,
}: {
  slot: SlotDefinition;
  job: JobRecord | null;
  photos: Photo[];
}) {
  const guess = photos.find((p) =>
    p.name.toLowerCase().includes(slot.photo_requirement.scene_kind.split("_")[0])
  );
  const previewSrc = guess ? backendURL(guess.url) : null;

  return (
    <div className="flex flex-col items-center gap-3 px-6 text-center text-neutral-500">
      {previewSrc && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={previewSrc}
          alt={slot.label}
          className="h-32 w-auto rounded-md object-cover opacity-50"
        />
      )}
      <div className="flex flex-col items-center gap-1">
        <p className="text-sm font-medium text-neutral-100">{slot.label}</p>
        <p className="text-xs text-neutral-400">
          {job ? jobStatusLabel(job) : "Starting…"}
        </p>
        {job?.status === "error" && (
          <p className="mt-2 max-w-md rounded border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300">
            {job.error}
          </p>
        )}
      </div>
      {job && job.status !== "error" && (
        <div className="h-1.5 w-48 overflow-hidden rounded-full bg-neutral-800">
          <div
            className="h-full bg-blue-500 transition-[width] duration-500"
            style={{ width: `${job.percent}%` }}
          />
        </div>
      )}
    </div>
  );
}

/* ─── Slot strip ────────────────────────────────────────────────────────── */

function SlotStrip({
  slots,
  photos,
  slotJobs,
  selectedSlotId,
  onSlotClick,
  assignmentBySlot,
}: {
  slots: SlotDefinition[];
  photos: Photo[];
  slotJobs: Record<string, string>;
  selectedSlotId: string | null;
  onSlotClick: (slot: SlotDefinition) => void;
  assignmentBySlot: Record<string, PlanSlotAssignment>;
}) {
  return (
    <div className="shrink-0 rounded-xl border border-neutral-800 bg-neutral-900 p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs uppercase tracking-wider text-neutral-500">
          Timeline · {slots.length} slot{slots.length === 1 ? "" : "s"}
        </p>
        <p className="text-xs text-neutral-500">
          Click a slot to generate it
        </p>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {slots.map((slot, idx) => (
          <SlotThumb
            key={slot.id}
            slot={slot}
            index={idx}
            photos={photos}
            jobId={slotJobs[slot.id] ?? null}
            isSelected={selectedSlotId === slot.id}
            assignment={assignmentBySlot[slot.id] ?? null}
            onClick={() => onSlotClick(slot)}
          />
        ))}
      </div>
    </div>
  );
}

const SLOT_THUMB_WIDTH_PX = 160;

function SlotThumb({
  slot,
  index,
  photos,
  jobId,
  isSelected,
  assignment,
  onClick,
}: {
  slot: SlotDefinition;
  index: number;
  photos: Photo[];
  jobId: string | null;
  isSelected: boolean;
  assignment: PlanSlotAssignment | null;
  onClick: () => void;
}) {
  const { job } = useJob(jobId);
  const sceneKind = slot.photo_requirement.scene_kind;

  // Preference order for the preview image, from authoritative to fallback:
  //   1. Job's rendered start frame (if the slot has run)
  //   2. Plan's primary photo for this slot (post-classification)
  //   3. Filename guess (pre-classification)
  //   4. None (no photo for this scene_kind)
  const renderedStart = job?.start_frame_url
    ? backendURL(job.start_frame_url)
    : null;
  const planPrimary = assignment?.primary_photo_path
    ? `/inputs/${assignment.primary_photo_path.split("/").slice(-2).join("/")}`
    : null;
  const planPhoto = planPrimary
    ? photos.find((p) => p.url === planPrimary)
    : null;
  const guess = photos.find((p) =>
    p.name.toLowerCase().includes(sceneKind.split("_")[0])
  );
  const previewSrc =
    renderedStart ??
    (planPhoto ? backendURL(planPhoto.url) : null) ??
    (guess ? backendURL(guess.url) : null);

  // Inactive slots from the plan: dimmed and not strongly clickable.
  const isInactive = assignment?.is_active === false;

  // States that drive the visual affordance.
  const isDone = job?.status === "done";
  const isRunning = job !== null && !isDone && job?.status !== "error";
  const isIdle = job === null;

  return (
    <button
      onClick={onClick}
      className={`group flex shrink-0 cursor-pointer flex-col gap-1 rounded-md text-left transition-transform hover:-translate-y-0.5 ${
        isSelected
          ? "ring-2 ring-blue-500 ring-offset-2 ring-offset-neutral-900"
          : ""
      } ${isInactive ? "opacity-50 hover:opacity-70" : ""}`}
      style={{ width: SLOT_THUMB_WIDTH_PX }}
      title={
        isInactive
          ? `${slot.label} — no photo for ${sceneKind}, slot inactive`
          : isDone
            ? `${slot.label} — done. Click to play.`
            : isRunning
              ? `${slot.label} — running…`
              : `${slot.label} — click to generate`
      }
    >
      <div className="relative aspect-[16/9] overflow-hidden rounded-md bg-neutral-800 ring-1 ring-neutral-800 transition group-hover:ring-blue-500">
        {previewSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewSrc}
            alt={slot.label}
            className={`h-full w-full object-cover ${isDone ? "" : "opacity-70"}`}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-neutral-600">
            (no photo)
          </div>
        )}
        <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-neutral-300">
          {String(index + 1).padStart(2, "0")}
        </span>
        <SlotStatusBadge job={job} />

        {/* Hover affordance — only when idle and active. Tells the user
            the click action is "generate this slot". */}
        {isIdle && !isInactive && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 transition group-hover:bg-black/55">
            <div className="flex translate-y-1 scale-90 items-center gap-1.5 rounded-full bg-blue-600 px-3 py-1.5 text-[11px] font-medium text-white opacity-0 shadow-lg transition group-hover:translate-y-0 group-hover:scale-100 group-hover:opacity-100">
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                className="h-3 w-3"
              >
                <path d="M8 5v14l11-7L8 5z" />
              </svg>
              Generate
            </div>
          </div>
        )}
        {/* Hover affordance for done slots — tells the user clicking plays. */}
        {isDone && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 transition group-hover:bg-black/55">
            <div className="flex translate-y-1 scale-90 items-center gap-1.5 rounded-full bg-emerald-600 px-3 py-1.5 text-[11px] font-medium text-white opacity-0 shadow-lg transition group-hover:translate-y-0 group-hover:scale-100 group-hover:opacity-100">
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                className="h-3 w-3"
              >
                <path d="M8 5v14l11-7L8 5z" />
              </svg>
              Play
            </div>
          </div>
        )}
      </div>
      <p className="truncate text-[11px] text-neutral-300" title={slot.label}>
        {slot.label}
      </p>
      <p className="truncate text-[10px] text-neutral-500">{sceneKind}</p>
    </button>
  );
}

function SlotStatusBadge({ job }: { job: JobRecord | null }) {
  if (!job) return null;
  if (job.status === "done") {
    return (
      <span className="absolute right-1 top-1 rounded-full bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
        ✓
      </span>
    );
  }
  if (job.status === "error") {
    return (
      <span className="absolute right-1 top-1 rounded-full bg-red-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
        !
      </span>
    );
  }
  // In-flight: show percent
  return (
    <>
      <span className="absolute right-1 top-1 rounded bg-black/80 px-1.5 py-0.5 text-[10px] font-medium text-blue-300">
        {job.percent}%
      </span>
      <div className="absolute inset-x-0 bottom-0 h-1 bg-neutral-900">
        <div
          className="h-full bg-blue-500 transition-[width] duration-500"
          style={{ width: `${job.percent}%` }}
        />
      </div>
    </>
  );
}

function jobStatusLabel(job: JobRecord): string {
  switch (job.status) {
    case "queued":
      return "Queued…";
    case "classifying":
      return "Classifying photos…";
    case "image_pass":
      return "Running image pipeline…";
    case "end_frame":
      return "Synthesizing end frame…";
    case "video_pass":
      return "Generating video clip…";
    case "done":
      return "Done";
    case "error":
      return "Error";
    default:
      return job.message;
  }
}

/* ─── Right panel ───────────────────────────────────────────────────────── */

function RightPanel({
  template,
  slotJobs,
  selectedSlotId,
  onPreviewImage,
}: {
  template: TemplateFull;
  slotJobs: Record<string, string>;
  selectedSlotId: string | null;
  onPreviewImage: (url: string, label: string) => void;
}) {
  const selectedJobId = selectedSlotId ? slotJobs[selectedSlotId] ?? null : null;
  const { job } = useJob(selectedJobId);

  return (
    <aside className="flex min-h-0 flex-col rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="shrink-0 border-b border-neutral-800 px-4 py-3">
        <div className="flex gap-1 rounded-lg bg-neutral-950 p-1 text-xs">
          <span className="rounded-md bg-neutral-800 px-3 py-1 text-neutral-100">History</span>
          <span className="px-3 py-1 text-neutral-500">Presets</span>
          <span className="px-3 py-1 text-neutral-500">Effects</span>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto p-3 text-sm">
        {selectedSlotId && job ? (
          <SelectedSlotDetails
            template={template}
            job={job}
            slotId={selectedSlotId}
            onPreviewImage={onPreviewImage}
          />
        ) : (
          <TemplateDetails template={template} />
        )}
      </div>
    </aside>
  );
}

function TemplateDetails({ template }: { template: TemplateFull }) {
  return (
    <>
      <p className="mb-3 text-xs uppercase tracking-wider text-neutral-500">
        Template details
      </p>
      <p className="mb-2 text-neutral-100">{template.template.name}</p>
      <p className="mb-3 text-xs text-neutral-400">
        {template.template.description ?? "No description."}
      </p>
      <p className="text-xs text-neutral-500">
        {template.slots.length} slot{template.slots.length === 1 ? "" : "s"} ·{" "}
        {template.template.duration_sec}s · {template.template.aspect_ratio}
      </p>
    </>
  );
}

function SelectedSlotDetails({
  template,
  job,
  slotId,
  onPreviewImage,
}: {
  template: TemplateFull;
  job: JobRecord;
  slotId: string;
  onPreviewImage: (url: string, label: string) => void;
}) {
  const slot = template.slots.find((s) => s.id === slotId);
  return (
    <>
      <p className="mb-3 text-xs uppercase tracking-wider text-neutral-500">
        Slot run
      </p>
      <p className="mb-1 text-neutral-100">{slot?.label ?? slotId}</p>
      <p className="mb-3 text-xs text-neutral-400">
        Status: <span className="text-neutral-200">{jobStatusLabel(job)}</span>
      </p>

      <DetailRow label="Strategy" value={job.strategy ?? "—"} />
      <DetailRow label="Model" value={job.model_used ?? "—"} mono />
      <DetailRow
        label="Primary"
        value={job.primary_photo_path ? fileBasename(job.primary_photo_path) : "—"}
        mono
      />
      <DetailRow
        label="End frame"
        value={
          job.end_frame_photo_path
            ? fileBasename(job.end_frame_photo_path)
            : job.end_frame_url
              ? "synthesized"
              : "—"
        }
        mono
      />
      {job.reference_paths.length > 0 && (
        <DetailRow
          label="Refs"
          value={job.reference_paths.map(fileBasename).join(", ")}
          mono
        />
      )}

      {(job.start_frame_url || job.end_frame_url) && (
        <div className="mt-4">
          <p className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
            Frames
          </p>
          <div className="grid grid-cols-2 gap-2">
            {job.start_frame_url && (
              <FramePreview
                url={job.start_frame_url}
                label="start"
                onPreviewImage={onPreviewImage}
              />
            )}
            {job.end_frame_url && (
              <FramePreview
                url={job.end_frame_url}
                label="end"
                onPreviewImage={onPreviewImage}
              />
            )}
          </div>
        </div>
      )}

      {job.error && (
        <div className="mt-4 rounded border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300">
          {job.error}
        </div>
      )}
    </>
  );
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="mb-1 flex justify-between gap-3 text-xs">
      <span className="shrink-0 text-neutral-500">{label}</span>
      <span
        className={`min-w-0 truncate text-right text-neutral-300 ${
          mono ? "font-mono" : ""
        }`}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function FramePreview({
  url,
  label,
  onPreviewImage,
}: {
  url: string;
  label: string;
  onPreviewImage: (url: string, label: string) => void;
}) {
  const fullUrl = backendURL(url);
  return (
    <button
      onClick={() => onPreviewImage(fullUrl, `${label} frame`)}
      className="group overflow-hidden rounded-md bg-neutral-800 ring-1 ring-transparent transition hover:ring-blue-500"
      title={`Preview ${label} frame`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={fullUrl}
        alt={label}
        className="aspect-video w-full object-cover transition group-hover:scale-[1.02]"
      />
      <p className="px-2 py-1 text-center text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </p>
    </button>
  );
}

function fileBasename(p: string): string {
  return p.split("/").pop() ?? p;
}
