"use client";

import { useRef } from "react";

import { Project, TemplateSummary, backendURL } from "@/lib/api";

type Props = {
  projects: Project[];
  templates: TemplateSummary[];
  onSelectProject: (project: Project) => void;
  onSelectTemplate: (template: TemplateSummary) => void;
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
 *   1. PROJECTS — existing projects (subfolders of inputs/) plus a "New
 *      project from blank" tile at the very front. Clicking a project tile
 *      opens it preloaded with the template it was created with.
 *
 *   2. CREATE BY STYLE — the template gallery. Clicking a style tile starts
 *      a fresh project with that style. (No project is associated until the
 *      user uploads photos / picks a project to attach.)
 *
 * "New from blank" scrolls to the styles section.
 */
export default function TemplateGallery({
  projects,
  templates,
  onSelectProject,
  onSelectTemplate,
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
                onSelect={() => onSelectProject(p)}
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
              Pick a style to start a new project. Upload photos after.
            </p>
          </header>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((t, idx) => (
              <TemplateTile
                key={t.id}
                template={t}
                gradient={TILE_GRADIENTS[idx % TILE_GRADIENTS.length]}
                decor={decorFor(t.id)}
                onSelect={() => onSelectTemplate(t)}
              />
            ))}
          </div>
        </section>
      </div>
    </main>
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
  onSelect,
}: {
  project: Project;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className="group relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900 text-left transition hover:border-neutral-700"
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
        <div className="absolute inset-x-0 bottom-0 p-4">
          <h3 className="text-sm font-semibold text-white">{project.name}</h3>
          <p className="text-xs text-neutral-300">
            {project.photo_count} photo{project.photo_count === 1 ? "" : "s"}
          </p>
        </div>
      </div>
    </button>
  );
}

/* ─── Style / template tiles ────────────────────────────────────────────── */

function TemplateTile({
  template,
  gradient,
  decor,
  onSelect,
}: {
  template: TemplateSummary;
  gradient: string;
  decor: Decor;
  onSelect: () => void;
}) {
  const disabled = !template.enabled;
  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      className="group relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900 text-left transition hover:border-neutral-700 disabled:cursor-not-allowed disabled:opacity-70"
    >
      <div
        className={`relative aspect-[16/10] w-full bg-gradient-to-br ${gradient}`}
      >
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(255,255,255,0.15),_transparent_70%)]" />
        <div className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1 text-xs text-white backdrop-blur-sm">
          {decor.badge}
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-white/90 text-neutral-900 shadow-lg transition group-hover:scale-110">
            <svg viewBox="0 0 24 24" fill="currentColor" className="ml-1 h-6 w-6">
              <path d="M8 5v14l11-7L8 5z" />
            </svg>
          </div>
        </div>
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
