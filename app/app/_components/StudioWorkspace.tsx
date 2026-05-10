"use client";

import { useState } from "react";

import {
  JobRecord,
  Photo,
  SlotDefinition,
  TemplateFull,
  TemplateSummary,
  backendURL,
  runSlot,
} from "@/lib/api";
import { useJob } from "@/lib/useJob";

type Props = {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  selectedProjectName: string | null;
  selectedProjectSlug: string | null;
  onBackToProjects: () => void;
};

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
}: Props) {
  const [slotJobs, setSlotJobs] = useState<Record<string, string>>({});
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // Click handler: selects the slot for the player; if no job has been
  // kicked off for this slot yet, start one.
  const handleSlotClick = async (slot: SlotDefinition) => {
    setSelectedSlotId(slot.id);
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

  return (
    <main className="grid min-h-0 flex-1 grid-cols-[minmax(220px,_280px)_minmax(0,_1fr)_minmax(220px,_280px)] gap-3 overflow-hidden bg-neutral-950 p-3">
      <PhotosPanel photos={photos} projectName={selectedProjectName} />
      <CenterPanel
        template={template}
        templateSummary={templateSummary}
        photos={photos}
        projectName={selectedProjectName}
        onBackToProjects={onBackToProjects}
        slotJobs={slotJobs}
        selectedSlotId={selectedSlotId}
        onSlotClick={handleSlotClick}
        startError={startError}
      />
      <RightPanel
        template={template}
        slotJobs={slotJobs}
        selectedSlotId={selectedSlotId}
      />
    </main>
  );
}

/* ─── Photos panel ──────────────────────────────────────────────────────── */

function PhotosPanel({
  photos,
  projectName,
}: {
  photos: Photo[];
  projectName: string | null;
}) {
  return (
    <aside className="flex min-h-0 flex-col rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-4 py-3">
        <div className="flex gap-1 rounded-lg bg-neutral-950 p-1 text-xs">
          <span className="rounded-md bg-neutral-800 px-3 py-1 text-neutral-100">Photos</span>
          <span className="px-3 py-1 text-neutral-500">Video</span>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto p-3">
        <div className="grid grid-cols-2 gap-2">
          <button
            className="flex aspect-square flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed border-neutral-700 text-neutral-500 transition hover:border-neutral-500 hover:text-neutral-200"
            title="Add photos"
          >
            <span className="text-2xl leading-none">+</span>
            <span className="text-[10px]">Add photos</span>
          </button>
          {photos.map((p) => (
            <div
              key={p.url}
              className="aspect-square overflow-hidden rounded-md bg-neutral-800"
              title={p.name}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={backendURL(p.url)}
                alt={p.name}
                className="h-full w-full object-cover"
                loading="lazy"
              />
            </div>
          ))}
        </div>
        {photos.length === 0 && (
          <p className="mt-3 text-center text-xs text-neutral-500">
            {projectName ? (
              <>
                Drop photos in{" "}
                <code className="rounded bg-neutral-800 px-1 py-0.5 text-[11px]">
                  inputs/{projectName}/
                </code>
              </>
            ) : (
              <>Click + to add the first photo to this project</>
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
  selectedSlotId,
  onSlotClick,
  startError,
}: {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  projectName: string | null;
  onBackToProjects: () => void;
  slotJobs: Record<string, string>;
  selectedSlotId: string | null;
  onSlotClick: (slot: SlotDefinition) => void;
  startError: string | null;
}) {
  const slots = template.slots;
  const enabled = templateSummary.enabled;
  const isNewProject = !projectName;
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
        selectedSlot={selectedSlot}
        selectedSlotJobId={selectedSlotId ? slotJobs[selectedSlotId] ?? null : null}
        photos={photos}
        isNewProject={isNewProject}
        slotsCount={slots.length}
        enabled={enabled}
        startError={startError}
      />

      <SlotStrip
        slots={slots}
        photos={photos}
        slotJobs={slotJobs}
        selectedSlotId={selectedSlotId}
        onSlotClick={onSlotClick}
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
                : "Run the full template (template-wide generation comes next)"
          }
        >
          {isNewProject || photos.length === 0
            ? "Add images first"
            : `Generate · ${slots.length * 3} credits`}
        </button>
        <p className="text-xs text-neutral-500">
          Click any slot in the timeline to test it individually.
        </p>
      </div>
    </section>
  );
}

/* ─── Player area ───────────────────────────────────────────────────────── */

function PlayerArea({
  selectedSlot,
  selectedSlotJobId,
  photos,
  isNewProject,
  slotsCount,
  enabled,
  startError,
}: {
  selectedSlot: SlotDefinition | null;
  selectedSlotJobId: string | null;
  photos: Photo[];
  isNewProject: boolean;
  slotsCount: number;
  enabled: boolean;
  startError: string | null;
}) {
  const { job } = useJob(selectedSlotJobId);

  const hasVideo = job?.status === "done" && job.video_url;

  return (
    <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-xl border border-neutral-800 bg-black">
      {hasVideo ? (
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
        <SlotJobOverlay
          slot={selectedSlot}
          job={job}
          photos={photos}
        />
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
        {hasVideo ? "Rendered" : "Preview"}
      </span>
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
}: {
  slots: SlotDefinition[];
  photos: Photo[];
  slotJobs: Record<string, string>;
  selectedSlotId: string | null;
  onSlotClick: (slot: SlotDefinition) => void;
}) {
  return (
    <div className="shrink-0 rounded-xl border border-neutral-800 bg-neutral-900 p-3">
      <p className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
        Timeline · {slots.length} slot{slots.length === 1 ? "" : "s"}
      </p>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {slots.map((slot, idx) => (
          <SlotThumb
            key={slot.id}
            slot={slot}
            index={idx}
            photos={photos}
            jobId={slotJobs[slot.id] ?? null}
            isSelected={selectedSlotId === slot.id}
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
  onClick,
}: {
  slot: SlotDefinition;
  index: number;
  photos: Photo[];
  jobId: string | null;
  isSelected: boolean;
  onClick: () => void;
}) {
  const { job } = useJob(jobId);

  const sceneKind = slot.photo_requirement.scene_kind;
  // If the slot's job already produced a video, show its rendered start
  // frame (or just use a video poster) so the thumb visually distinguishes
  // a done slot from an idle one.
  const renderedStart = job?.start_frame_url
    ? backendURL(job.start_frame_url)
    : null;
  const guess = photos.find((p) =>
    p.name.toLowerCase().includes(sceneKind.split("_")[0])
  );
  const previewSrc = renderedStart ?? (guess ? backendURL(guess.url) : null);

  return (
    <button
      onClick={onClick}
      className={`flex shrink-0 flex-col gap-1 rounded-md text-left transition ${
        isSelected ? "ring-2 ring-blue-500 ring-offset-2 ring-offset-neutral-900" : ""
      }`}
      style={{ width: SLOT_THUMB_WIDTH_PX }}
      title={`${slot.label} (${sceneKind})`}
    >
      <div className="relative aspect-[16/9] overflow-hidden rounded-md bg-neutral-800">
        {previewSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewSrc}
            alt={slot.label}
            className={`h-full w-full object-cover ${job?.status === "done" ? "" : "opacity-70"}`}
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
}: {
  template: TemplateFull;
  slotJobs: Record<string, string>;
  selectedSlotId: string | null;
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
          <SelectedSlotDetails template={template} job={job} slotId={selectedSlotId} />
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
}: {
  template: TemplateFull;
  job: JobRecord;
  slotId: string;
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
              <FramePreview url={job.start_frame_url} label="start" />
            )}
            {job.end_frame_url && (
              <FramePreview url={job.end_frame_url} label="end" />
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

function FramePreview({ url, label }: { url: string; label: string }) {
  return (
    <div className="overflow-hidden rounded-md bg-neutral-800">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={backendURL(url)}
        alt={label}
        className="aspect-video w-full object-cover"
      />
      <p className="px-2 py-1 text-center text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </p>
    </div>
  );
}

function fileBasename(p: string): string {
  return p.split("/").pop() ?? p;
}
