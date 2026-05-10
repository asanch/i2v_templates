"use client";

import { Photo, SlotDefinition, TemplateFull, TemplateSummary, backendURL } from "@/lib/api";

type Props = {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  selectedProjectName: string | null;
  onBackToProjects: () => void;
};

/**
 * Three-pane studio. No page scroll; the only scrollable region is the
 * photos panel on the left. The center stack is fixed-height with the
 * video player taking the flexible middle and the slot strip + generate
 * bar pinned to the bottom of the viewport.
 *
 * Responsive grid:
 *   - left + right panels are clamped to 220-280px each.
 *   - middle uses `minmax(0, 1fr)` so it can shrink to nothing if the
 *     viewport gets really narrow without pushing the right pane off-screen.
 *   - both side panels stay visible; only the middle column collapses first.
 *
 * `min-h-0` / `min-w-0` on flex children let overflow-auto actually contain
 * children rather than blowing past the viewport.
 */
export default function StudioWorkspace({
  template,
  templateSummary,
  photos,
  selectedProjectName,
  onBackToProjects,
}: Props) {
  return (
    <main className="grid min-h-0 flex-1 grid-cols-[minmax(220px,_280px)_minmax(0,_1fr)_minmax(220px,_280px)] gap-3 overflow-hidden bg-neutral-950 p-3">
      <PhotosPanel photos={photos} projectName={selectedProjectName} />
      <CenterPanel
        template={template}
        templateSummary={templateSummary}
        photos={photos}
        projectName={selectedProjectName}
        onBackToProjects={onBackToProjects}
      />
      <RightPanel template={template} />
    </main>
  );
}

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
          {/* Add-photo tile is always the first thumbnail so it's reachable
              without scrolling. Clicking is a no-op until the upload
              endpoint lands. */}
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

function CenterPanel({
  template,
  templateSummary,
  photos,
  projectName,
  onBackToProjects,
}: {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  projectName: string | null;
  onBackToProjects: () => void;
}) {
  const slots = template.slots;
  const enabled = templateSummary.enabled;
  const isNewProject = !projectName;

  return (
    <section className="flex min-w-0 min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between gap-3 rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          {/* Back-to-projects affordance, sits immediately left of the project
              name so it's the obvious "exit this view" action. */}
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

      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-xl border border-neutral-800 bg-black">
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
                Drop photos in <code className="rounded bg-neutral-800 px-1 py-0.5 text-[11px]">inputs/&lt;your-project-name&gt;/</code>{" "}
                and refresh, or use the project switcher to attach an existing project.
              </p>
            </>
          ) : photos.length === 0 ? (
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
                  ? "Click Generate to produce the walkthrough"
                  : "This template is a stub. Pick Cinematic Editorial to actually render."}
              </p>
              <p className="text-xs">
                {photos.length} photos available · {slots.length} slots in template
              </p>
            </>
          )}
        </div>
        <span className="absolute right-3 top-3 rounded-full bg-black/60 px-2 py-1 text-xs text-neutral-300 backdrop-blur-sm">
          1080p
        </span>
      </div>

      <SlotStrip slots={slots} photos={photos} />

      <div className="flex shrink-0 items-center gap-3 rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-3">
        <button
          className="rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!enabled || photos.length === 0}
          title={
            !enabled
              ? "This template is a stub"
              : photos.length === 0
                ? "Add images to this project first"
                : "Run the full template (not yet wired)"
          }
        >
          {isNewProject || photos.length === 0
            ? "Add images first"
            : `Generate · ${slots.length * 3} credits`}
        </button>
        <p className="text-xs text-neutral-500">
          {!enabled
            ? "Stub template — generation is disabled."
            : isNewProject || photos.length === 0
              ? "Add at least one image to start generating."
              : "Generate runs classify → image pass → video pass per slot."}
        </p>
      </div>
    </section>
  );
}

function SlotStrip({
  slots,
  photos,
}: {
  slots: SlotDefinition[];
  photos: Photo[];
}) {
  return (
    <div className="shrink-0 rounded-xl border border-neutral-800 bg-neutral-900 p-3">
      <p className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
        Timeline · {slots.length} slot{slots.length === 1 ? "" : "s"}
      </p>
      {/* Horizontal scroll. Each thumb is locked to a fixed width so the
          row never reflows; thumbs that don't fit slide off the right edge
          and become reachable by horizontal scroll. */}
      <div className="flex gap-2 overflow-x-auto pb-2">
        {slots.map((slot, idx) => (
          <SlotThumb key={slot.id} slot={slot} index={idx} photos={photos} />
        ))}
      </div>
    </div>
  );
}

// Locked thumb dimensions. Same width for every slot regardless of label
// length or photo presence — keeps the row visually rhythmic.
const SLOT_THUMB_WIDTH_PX = 160;

function SlotThumb({
  slot,
  index,
  photos,
}: {
  slot: SlotDefinition;
  index: number;
  photos: Photo[];
}) {
  const sceneKind = slot.photo_requirement.scene_kind;
  const guess = photos.find((p) =>
    p.name.toLowerCase().includes(sceneKind.split("_")[0])
  );
  const previewSrc = guess ? backendURL(guess.url) : null;

  return (
    <div
      className="flex shrink-0 flex-col gap-1"
      style={{ width: SLOT_THUMB_WIDTH_PX }}
    >
      <div className="relative aspect-[16/9] overflow-hidden rounded-md bg-neutral-800">
        {previewSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewSrc}
            alt={slot.label}
            className="h-full w-full object-cover opacity-70"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-neutral-600">
            (no photo)
          </div>
        )}
        <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-neutral-300">
          {String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <p className="truncate text-[11px] text-neutral-300" title={slot.label}>
        {slot.label}
      </p>
      <p className="truncate text-[10px] text-neutral-500">{sceneKind}</p>
    </div>
  );
}

function RightPanel({ template }: { template: TemplateFull }) {
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
      </div>
    </aside>
  );
}
