"use client";

import { useEffect, useRef, useState } from "react";

import { Project, TemplateSummary, backendURL } from "@/lib/api";

type ProcessingState = {
  phase: "classifying" | "walkthrough" | "done" | "error";
  classifyJobId: string | null;
  templateJobId: string | null;
  error?: string;
};

type Props = {
  projects: Project[];
  templates: TemplateSummary[];
  /** Per-slug pipeline phase. Drives the spinner overlay + click-gating
   *  on the corresponding ProjectTile in the Projects section. */
  processingProjects: Record<string, ProcessingState>;
  onSelectProject: (project: Project) => void;
  onSelectTemplate: (template: TemplateSummary) => void;
  /** Auto-pipeline kicked off from the Create-by-Style section. Resolves
   *  with the new project slug once classification + walkthrough have
   *  been kicked off (walkthrough continues in background). */
  onCreateFromStyle: (
    name: string,
    template: TemplateSummary,
    files: File[],
  ) => Promise<string>;
  /** Permanently delete a project. The page handler shows a confirm
   *  dialog before calling through to the backend. */
  onDeleteProject: (project: Project) => Promise<void>;
};

const TILE_GRADIENTS = [
  "from-amber-700 via-rose-700 to-rose-900",
  "from-emerald-700 via-teal-700 to-slate-900",
  "from-sky-600 via-blue-800 to-indigo-900",
  "from-stone-600 via-amber-800 to-stone-900",
  "from-rose-700 via-pink-800 to-purple-900",
  "from-slate-600 via-zinc-700 to-zinc-900",
  "from-purple-700 via-fuchsia-800 to-violet-900",
  "from-cyan-600 via-sky-700 to-blue-900",
];

type Decor = {
  badge: string;
  creator: string;
  rating: number;
  reviews: number;
};

const TILE_DECOR: Record<string, Decor> = {
  "cinematic-editorial-v1": {
    badge: "Featured",
    creator: "AutoHDR",
    rating: 4.9,
    reviews: 312,
  },
  "golden-hour-dusk": {
    badge: "By JT Visuals",
    creator: "JT Visuals",
    rating: 4.7,
    reviews: 84,
  },
  "luxe-interior-living": {
    badge: "Editor's Pick",
    creator: "Hatch Studio",
    rating: 4.6,
    reviews: 142,
  },
  "modern-coastal-twilight": {
    badge: "Coastal",
    creator: "Halo Films",
    rating: 4.8,
    reviews: 198,
  },
  "mountain-retreat": {
    badge: "New",
    creator: "RidgeLine",
    rating: 4.4,
    reviews: 23,
  },
  "cozy-bohemian-nook": {
    badge: "Trending",
    creator: "Wonder Loft",
    rating: 4.5,
    reviews: 67,
  },
  "sleek-urban-loft": {
    badge: "Pro Pack",
    creator: "Metro Visuals",
    rating: 4.7,
    reviews: 105,
  },
};

function decorFor(id: string): Decor {
  return (
    TILE_DECOR[id] ?? {
      badge: "Style",
      creator: "AutoHDR",
      rating: 4.5,
      reviews: 50,
    }
  );
}

function renderStars(rating: number): string {
  const full = Math.floor(rating);
  const half = rating - full >= 0.5;
  return (
    "★".repeat(full) +
    (half ? "½" : "") +
    "☆".repeat(5 - full - (half ? 1 : 0))
  );
}

/**
 * Landing view with two stacked sections:
 *
 *   1. PROJECTS — existing projects (subfolders of inputs/) plus a
 *      "New project from blank" tile at the very front. A processing
 *      project (from the auto-pipeline) shows a spinner overlay on its
 *      tile until classification finishes; afterwards it's clickable
 *      and the walkthrough continues rendering in the background.
 *
 *   2. CREATE BY STYLE — two-step wizard:
 *        Step 1 · Upload photos
 *        Step 2 · Pick a style
 *      Both must be satisfied before the "Create Project" CTA enables.
 *      On click, the page-level orchestrator runs:
 *        createProject → uploadPhotos → classifyProject → runTemplate
 *      and a new tile appears in the Projects section with a spinner.
 */
export default function TemplateGallery({
  projects,
  templates,
  processingProjects,
  onSelectProject,
  onSelectTemplate,
  onCreateFromStyle,
  onDeleteProject,
}: Props) {
  const stylesRef = useRef<HTMLDivElement | null>(null);

  const scrollToStyles = () => {
    stylesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <main className="flex-1 min-h-0 overflow-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-6 py-10">
        {/* ─── Projects section ───────────────────────────────────────── */}
        <section className="space-y-4">
          <header className="flex items-end justify-between">
            <h2 className="text-2xl font-semibold tracking-tight text-neutral-100">
              Projects
            </h2>
            <p className="text-xs text-neutral-500">
              {projects.length} ongoing
            </p>
          </header>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <NewFromBlankTile onClick={scrollToStyles} />
            {projects.map((p) => (
              <ProjectTile
                key={p.slug}
                project={p}
                processing={processingProjects[p.slug] ?? null}
                onSelect={() => onSelectProject(p)}
                onDelete={() => onDeleteProject(p)}
              />
            ))}
          </div>
        </section>

        {/* ─── Create by style section ────────────────────────────────── */}
        <section ref={stylesRef} className="space-y-4">
          <header className="space-y-1">
            <h2 className="text-2xl font-semibold tracking-tight text-neutral-100">
              Create by style
            </h2>
            <p className="text-sm text-neutral-400">
              Upload photos, pick a style, and we'll generate a full walkthrough
              for you.
            </p>
          </header>

          <CreateByStyleWizard
            templates={templates}
            onCreate={onCreateFromStyle}
          />
        </section>
      </div>
    </main>
  );
}

/* ─── Create-by-Style wizard ───────────────────────────────────────────── */

function CreateByStyleWizard({
  templates,
  onCreate,
}: {
  templates: TemplateSummary[];
  onCreate: (
    name: string,
    template: TemplateSummary,
    files: File[],
  ) => Promise<string>;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedTemplate =
    templates.find((t) => t.id === selectedTemplateId) ?? null;

  const enabledTemplates = templates.filter((t) => t.enabled);

  const canSubmit =
    files.length > 0 &&
    selectedTemplate !== null &&
    selectedTemplate.enabled &&
    !submitting;

  const addFiles = (newFiles: FileList | File[]) => {
    setError(null);
    const arr = Array.from(newFiles).filter((f) =>
      f.type.startsWith("image/") || /\.(heic|webp|jpe?g|png)$/i.test(f.name),
    );
    setFiles((prev) => [...prev, ...arr]);
  };

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleCreate = async () => {
    if (!canSubmit || !selectedTemplate) return;
    // Auto-name the project so the user doesn't have to wrangle a browser
    // prompt for it. Format: "<Template name> · <Mon D, h:mma>" — the
    // template is recognizable, the timestamp prevents collisions, and
    // the tile's cover photo + template badge already tell the user
    // which property is which at a glance.
    const now = new Date();
    const datePart = now.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    const timePart = now
      .toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      })
      .replace(/\s/g, "")
      .toLowerCase();
    const name = `${selectedTemplate.name} · ${datePart} ${timePart}`;

    setSubmitting(true);
    setError(null);
    try {
      await onCreate(name, selectedTemplate, files);
      // Clear local state after success — the new tile is in the Projects
      // section, the user has visual confirmation, and they can build
      // another walkthrough from a different style without state leaking.
      setFiles([]);
      setSelectedTemplateId(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Step 1 — Upload photos */}
      <div className="space-y-3">
        <StepHeader
          number="1"
          title="Upload photos"
          subtitle="Drop in the property photos. The classifier will assign each one to a slot."
          done={files.length > 0}
        />
        <PhotoUploadGrid
          files={files}
          onAdd={addFiles}
          onRemove={removeFile}
          disabled={submitting}
        />
      </div>

      {/* Step 2 — Pick a style */}
      <div className="space-y-3">
        <StepHeader
          number="2"
          title="Pick a style"
          subtitle="The walkthrough will be graded in this aesthetic from end to end."
          done={selectedTemplate !== null && selectedTemplate.enabled}
        />
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {templates.map((t, idx) => (
            <TemplateTile
              key={t.id}
              template={t}
              gradient={TILE_GRADIENTS[idx % TILE_GRADIENTS.length]}
              decor={decorFor(t.id)}
              selected={selectedTemplateId === t.id}
              onSelect={() => {
                if (!t.enabled) return;
                setSelectedTemplateId(
                  selectedTemplateId === t.id ? null : t.id,
                );
              }}
            />
          ))}
        </div>
        {enabledTemplates.length === 0 && (
          <p className="text-xs text-neutral-500">
            No styles enabled yet. Add an enabled template to{" "}
            <code className="rounded bg-neutral-800 px-1 py-0.5">templates/</code>.
          </p>
        )}
      </div>

      {/* Step 3 — CTA. The step-1 / step-2 checkmarks above already convey
          requirements; the disabled state of the button reinforces it.
          No redundant hint line needed. */}
      <div className="flex justify-end">
        <button
          onClick={handleCreate}
          disabled={!canSubmit}
          className="rounded-full bg-blue-600 px-6 py-2.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          title={
            !canSubmit
              ? files.length === 0
                ? "Upload at least one photo first"
                : !selectedTemplate
                  ? "Pick a style first"
                  : "Selected style isn't runnable yet"
              : `Render ${files.length} photo${files.length === 1 ? "" : "s"} as ${selectedTemplate?.name}`
          }
        >
          {submitting ? "Starting…" : "Create Project"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}

function StepHeader({
  number,
  title,
  subtitle,
  done,
}: {
  number: string;
  title: string;
  subtitle: string;
  done: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition ${
          done
            ? "bg-emerald-600 text-white"
            : "bg-neutral-800 text-neutral-400"
        }`}
      >
        {done ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" className="h-3.5 w-3.5">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          number
        )}
      </div>
      <div className="min-w-0">
        <p className="text-base font-semibold text-neutral-100">{title}</p>
        <p className="text-xs text-neutral-500">{subtitle}</p>
      </div>
    </div>
  );
}

/* ─── Photo upload grid ────────────────────────────────────────────────── */

function PhotoUploadGrid({
  files,
  onAdd,
  onRemove,
  disabled,
}: {
  files: File[];
  onAdd: (files: FileList | File[]) => void;
  onRemove: (idx: number) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [previews, setPreviews] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);

  // Generate object-URL previews for files; revoke on cleanup so the
  // browser doesn't leak blob handles between renders.
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [files]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    if (e.dataTransfer.files.length > 0) onAdd(e.dataTransfer.files);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      className={`grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 ${
        dragOver ? "ring-2 ring-blue-700/60" : ""
      } rounded-2xl`}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/heic"
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) {
            onAdd(e.target.files);
            e.target.value = "";
          }
        }}
      />
      {/* Upload tile — same dashed outline as New-from-Blank, sized as a
          tile like the user requested. Always first so the affordance is
          unmistakeable even with thumbnails next to it. */}
      <button
        type="button"
        onClick={() => !disabled && inputRef.current?.click()}
        disabled={disabled}
        className="group flex aspect-[16/10] flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-neutral-700 bg-neutral-950 text-neutral-400 transition hover:border-neutral-500 hover:bg-neutral-900 hover:text-neutral-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-dashed border-neutral-700 text-3xl group-hover:border-neutral-500">
          +
        </div>
        <p className="text-sm font-medium">Add photos</p>
        <p className="px-3 text-center text-[11px] text-neutral-500">
          {files.length === 0
            ? "Drag & drop or click"
            : `${files.length} added · click to add more`}
        </p>
      </button>

      {previews.map((url, i) => (
        <div
          key={url}
          className="group relative aspect-[16/10] overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={files[i]?.name ?? ""}
            className="h-full w-full object-cover"
          />
          <button
            onClick={() => onRemove(i)}
            disabled={disabled}
            title="Remove this photo"
            className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-black/70 text-xs text-white opacity-0 transition hover:bg-red-600 group-hover:opacity-100 disabled:opacity-0"
          >
            ×
          </button>
          <div className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/80 to-transparent px-2 py-1 text-[10px] text-neutral-200">
            {files[i]?.name}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── Project tiles ─────────────────────────────────────────────────────── */

function NewFromBlankTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group flex aspect-[16/10] flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-neutral-700 bg-neutral-950 text-neutral-400 transition hover:border-neutral-500 hover:bg-neutral-900 hover:text-neutral-100"
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-dashed border-neutral-700 text-3xl group-hover:border-neutral-500">
        +
      </div>
      <p className="text-sm font-medium">New project from blank</p>
      <p className="text-xs text-neutral-500">Pick a style below</p>
    </button>
  );
}

function ProjectTile({
  project,
  processing,
  onSelect,
  onDelete,
}: {
  project: Project;
  processing: ProcessingState | null;
  onSelect: () => void;
  onDelete: () => void;
}) {
  // The tile is non-clickable while classification is still in flight.
  // Once classification is done (phase becomes "walkthrough" or later)
  // the user can click in to watch the walkthrough render in real time.
  const isClassifying = processing?.phase === "classifying";
  const isWalkthroughInFlight = processing?.phase === "walkthrough";
  const hasError = processing?.phase === "error";
  const disabled = isClassifying;

  const overlayLabel = isClassifying
    ? "Classifying photos…"
    : isWalkthroughInFlight
      ? "Generating walkthrough…"
      : hasError
        ? "Pipeline failed"
        : null;

  // Pre-emptive stopPropagation so clicking the X doesn't also fire
  // onSelect (which would navigate the user into the studio mid-delete).
  const handleDeleteClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onDelete();
  };

  // The whole tile being a single <button> makes nesting another
  // <button> for delete invalid HTML, so we use a div+role for the
  // outer interactive surface. That also lets us layer the X button
  // as a separate clickable region.
  return (
    <div
      onClick={() => !disabled && onSelect()}
      role="button"
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`group relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900 text-left transition hover:border-neutral-700 ${
        disabled ? "cursor-not-allowed" : "cursor-pointer"
      }`}
      aria-disabled={disabled}
    >
      <div className="relative aspect-[16/10] w-full overflow-hidden bg-neutral-800">
        {project.cover_photo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={backendURL(project.cover_photo_url)}
            alt={project.name}
            className="h-full w-full object-cover transition group-hover:scale-105"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-neutral-500">
            No photos
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent" />
        <div className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1 text-xs text-white backdrop-blur-sm">
          {project.template_name}
        </div>

        {/* Delete button — hover-revealed top-left so it doesn't compete
            with the template badge top-right. Confirmation dialog lives
            in the page-level handler. */}
        <button
          onClick={handleDeleteClick}
          title={`Delete project "${project.name}"`}
          aria-label="Delete project"
          className="absolute left-3 top-3 z-10 flex h-7 w-7 items-center justify-center rounded-full bg-black/70 text-white opacity-0 transition hover:bg-red-600 group-hover:opacity-100 focus:opacity-100"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-3.5 w-3.5"
          >
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="18" y1="6" x2="6" y2="18" />
          </svg>
        </button>

        <div className="absolute inset-x-0 bottom-0 p-4">
          <h3 className="text-sm font-semibold text-white">{project.name}</h3>
          <p className="text-xs text-neutral-300">
            {project.photo_count} photo{project.photo_count === 1 ? "" : "s"}
          </p>
        </div>

        {overlayLabel && (
          <div
            className={`absolute inset-0 flex flex-col items-center justify-center gap-3 text-center backdrop-blur-sm ${
              hasError ? "bg-red-950/60" : "bg-black/70"
            }`}
          >
            {!hasError && (
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-neutral-600 border-t-blue-400" />
            )}
            <p className="text-sm font-medium text-white">{overlayLabel}</p>
            {isWalkthroughInFlight && (
              <p className="text-[11px] text-neutral-300">
                Click to watch progress in the studio
              </p>
            )}
            {hasError && (
              <p className="max-w-[80%] text-[11px] text-red-200">
                {processing?.error ?? "Unknown error"}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Style / template tiles ────────────────────────────────────────────── */

function TemplateTile({
  template,
  gradient,
  decor,
  selected,
  onSelect,
}: {
  template: TemplateSummary;
  gradient: string;
  decor: Decor;
  selected: boolean;
  onSelect: () => void;
}) {
  const disabled = !template.enabled;
  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      className={`group relative overflow-hidden rounded-2xl border text-left transition disabled:cursor-not-allowed disabled:opacity-70 ${
        selected
          ? "border-blue-500 bg-neutral-900 shadow-[0_0_0_2px_rgba(59,130,246,0.45)]"
          : "border-neutral-800 bg-neutral-900 hover:border-neutral-700"
      }`}
    >
      <div
        className={`relative aspect-[16/10] w-full bg-gradient-to-br ${gradient} ${
          selected ? "" : "transition group-hover:saturate-110"
        }`}
      >
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(255,255,255,0.15),_transparent_70%)]" />
        <div className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1 text-xs text-white backdrop-blur-sm">
          {decor.badge}
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div
            className={`flex h-14 w-14 items-center justify-center rounded-full shadow-lg transition ${
              selected
                ? "bg-blue-500 text-white scale-110"
                : "bg-white/90 text-neutral-900 group-hover:scale-110"
            }`}
          >
            {selected ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                className="h-6 w-6"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="currentColor" className="ml-1 h-6 w-6">
                <path d="M8 5v14l11-7L8 5z" />
              </svg>
            )}
          </div>
        </div>
        {selected && (
          <div className="absolute left-3 top-3 rounded-full bg-blue-600 px-3 py-1 text-[11px] font-medium text-white shadow">
            Selected
          </div>
        )}
        {disabled && (
          <div className="absolute inset-x-0 bottom-0 bg-black/60 px-3 py-2 text-center text-xs text-white backdrop-blur-sm">
            Coming soon
          </div>
        )}
      </div>
      <div className="space-y-1 p-4">
        <h3 className="text-sm font-medium text-neutral-100">{template.name}</h3>
        <div className="flex items-center gap-2 text-xs text-neutral-400">
          <span className="text-amber-400">{renderStars(decor.rating)}</span>
          <span>{decor.rating.toFixed(1)}</span>
          <span>+{decor.reviews} reviews</span>
        </div>
        <p className="text-xs text-neutral-500">
          Creator: <span className="text-neutral-300">{decor.creator}</span>
        </p>
      </div>
    </button>
  );
}
