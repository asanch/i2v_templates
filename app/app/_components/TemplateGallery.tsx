"use client";

import { TemplateSummary } from "@/lib/api";

type Props = {
  templates: TemplateSummary[];
  onSelect: (template: TemplateSummary) => void;
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

/**
 * Initial-state view: a grid of selectable template tiles. Matches the
 * "Styles for editing an entire property tour" mockup. The lone enabled
 * template (cinematic-editorial-v1) gets the active state; stubs are
 * rendered with a "Coming soon" overlay.
 */
export default function TemplateGallery({ templates, onSelect }: Props) {
  return (
    <main className="mx-auto flex max-w-6xl flex-col items-center gap-12 px-6 py-12">
      <header className="space-y-2 text-center">
        <h1 className="text-4xl font-semibold tracking-tight text-neutral-100">
          Styles for editing
          <br />
          an entire property tour
        </h1>
        <p className="text-sm text-neutral-400">
          Pick a style, upload your photos. Get a cinematic walkthrough.
        </p>
      </header>

      <section className="grid w-full grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {templates.map((t, idx) => (
          <TemplateTile
            key={t.id}
            template={t}
            gradient={TILE_GRADIENTS[idx % TILE_GRADIENTS.length]}
            onSelect={() => onSelect(t)}
          />
        ))}
      </section>

      <footer className="mt-4 text-xs text-neutral-500">
        {templates.length} {templates.length === 1 ? "style" : "styles"} available
      </footer>
    </main>
  );
}

function TemplateTile({
  template,
  gradient,
  onSelect,
}: {
  template: TemplateSummary;
  gradient: string;
  onSelect: () => void;
}) {
  const disabled = !template.enabled;
  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      className={`group relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900 text-left transition hover:border-neutral-700 disabled:cursor-not-allowed disabled:opacity-70`}
    >
      <div
        className={`relative aspect-[16/10] w-full bg-gradient-to-br ${gradient}`}
      >
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(255,255,255,0.15),_transparent_70%)]" />
        <div className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1 text-xs text-white backdrop-blur-sm">
          The Lisa
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-white/90 text-neutral-900 shadow-lg transition group-hover:scale-110">
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              className="ml-1 h-6 w-6"
            >
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
          <span>★★★★★</span>
          <span>4.8</span>
          <span>+20 reviews</span>
        </div>
        <p className="text-xs text-neutral-500">
          Creator: <span className="text-neutral-300">AutoHDR</span>
        </p>
      </div>
    </button>
  );
}
