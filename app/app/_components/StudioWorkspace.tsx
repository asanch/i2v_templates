"use client";

import { Photo, SlotDefinition, TemplateFull, TemplateSummary, backendURL } from "@/lib/api";

type Props = {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
  selectedProjectName: string | null;
};

/**
 * Three-pane studio. Mirrors the AutoHDR Video Studio mockup:
 *   left   — uploaded media (project photos)
 *   center — video player + slot strip
 *   right  — history / presets / effects (placeholders for now)
 *
 * No render is wired up yet; this view is structural.
 */
export default function StudioWorkspace({
  template,
  templateSummary,
  photos,
  selectedProjectName,
}: Props) {
  return (
    <main className="grid h-[calc(100vh-57px)] grid-cols-[300px_1fr_300px] gap-3 bg-neutral-950 p-3">
      <PhotosPanel photos={photos} projectName={selectedProjectName} />
      <CenterPanel template={template} templateSummary={templateSummary} photos={photos} />
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
    <aside className="flex flex-col rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-3">
        <div className="flex gap-1 rounded-lg bg-neutral-950 p-1 text-xs">
          <span className="rounded-md bg-neutral-800 px-3 py-1 text-neutral-100">Photos</span>
          <span className="px-3 py-1 text-neutral-500">Video</span>
        </div>
      </div>
      <div className="flex-1 overflow-auto p-3">
        <p className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
          Uploaded media — {projectName ?? "select a project"}
        </p>
        {photos.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 p-8 text-center text-xs text-neutral-500">
            <div className="h-12 w-12 rounded-full border-2 border-dashed border-neutral-700" />
            <p>No photos in this project.</p>
            <p>Drop photos in inputs/&lt;project&gt;/ or pick another project.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
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
            <button className="flex aspect-square items-center justify-center rounded-md border border-dashed border-neutral-700 text-2xl text-neutral-500 hover:border-neutral-500 hover:text-neutral-300">
              +
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

function CenterPanel({
  template,
  templateSummary,
  photos,
}: {
  template: TemplateFull;
  templateSummary: TemplateSummary;
  photos: Photo[];
}) {
  const slots = template.slots;
  const enabled = templateSummary.enabled;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
        <p className="font-medium text-neutral-100">
          {templateSummary.name} <span className="text-neutral-500">— Untitled Project</span>
        </p>
        <p className="text-xs text-neutral-500">
          {slots.length} slots · {template.template.duration_sec}s · {template.template.aspect_ratio}
        </p>
      </div>

      <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-xl border border-neutral-800 bg-black">
        {/* Placeholder for the rendered video. Once a render lands, swap to <video src=...>. */}
        <div className="flex flex-col items-center gap-3 text-neutral-500">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-neutral-800">
            <svg viewBox="0 0 24 24" fill="currentColor" className="ml-1 h-7 w-7">
              <path d="M8 5v14l11-7L8 5z" />
            </svg>
          </div>
          <p className="text-sm">
            {enabled
              ? "Click Generate to produce the walkthrough"
              : "This template is a stub. Pick the Cinematic Editorial template to actually render."}
          </p>
          <p className="text-xs">{photos.length} photos available · {slots.length} slots in template</p>
        </div>
        <span className="absolute right-3 top-3 rounded-full bg-black/60 px-2 py-1 text-xs text-neutral-300 backdrop-blur-sm">
          1080p
        </span>
      </div>

      <SlotStrip slots={slots} photos={photos} />

      <div className="flex items-center gap-3 rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-3">
        <button
          className="rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!enabled || photos.length === 0}
          title={
            !enabled
              ? "This template is a stub"
              : photos.length === 0
                ? "Select a project with photos first"
                : "Run the full template (not yet wired)"
          }
        >
          Generate · {slots.length * 3} credits
        </button>
        <p className="text-xs text-neutral-500">
          {enabled
            ? "Generate runs classify → image pass → video pass per slot."
            : "Stub template — generation is disabled."}
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
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-3">
      <p className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
        Timeline · {slots.length} slot{slots.length === 1 ? "" : "s"}
      </p>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {slots.map((slot, idx) => (
          <SlotThumb key={slot.id} slot={slot} index={idx} photos={photos} />
        ))}
      </div>
    </div>
  );
}

function SlotThumb({
  slot,
  index,
  photos,
}: {
  slot: SlotDefinition;
  index: number;
  photos: Photo[];
}) {
  // Lightweight scene→photo guesser: pick the first photo whose filename
  // hints at the slot's scene_kind. Backend classifier will replace this
  // with a real assignment once we wire generation.
  const sceneKind = slot.photo_requirement.scene_kind;
  const guess = photos.find((p) =>
    p.name.toLowerCase().includes(sceneKind.split("_")[0])
  );
  const previewSrc = guess ? backendURL(guess.url) : null;

  return (
    <div className="flex min-w-[140px] flex-col gap-1">
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
    <aside className="flex flex-col rounded-xl border border-neutral-800 bg-neutral-900">
      <div className="border-b border-neutral-800 px-4 py-3">
        <div className="flex gap-1 rounded-lg bg-neutral-950 p-1 text-xs">
          <span className="rounded-md bg-neutral-800 px-3 py-1 text-neutral-100">History</span>
          <span className="px-3 py-1 text-neutral-500">Presets</span>
          <span className="px-3 py-1 text-neutral-500">Effects</span>
        </div>
      </div>
      <div className="flex-1 overflow-auto p-3 text-sm">
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
