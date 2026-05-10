"use client";

import { Project } from "@/lib/api";

type Props = {
  projects: Project[];
  selectedProjectSlug: string | null;
  onSelectProject: (slug: string) => void;
  onResetTemplate: () => void;
};

/**
 * Top nav. Brand on the left, project switcher in the middle, actions on
 * the right. The project switcher is a native <select> for reliability —
 * styled to match the dark theme.
 */
export default function TopNav({
  projects,
  selectedProjectSlug,
  onSelectProject,
  onResetTemplate,
}: Props) {
  return (
    <header className="flex items-center justify-between border-b border-neutral-800 bg-neutral-950 px-6 py-3">
      <div className="flex items-center gap-6">
        <button
          onClick={onResetTemplate}
          className="flex items-center gap-2 text-sm font-semibold tracking-tight"
          title="Back to template gallery"
        >
          <span className="inline-block h-6 w-6 rounded-md bg-gradient-to-br from-blue-500 to-sky-700" />
          AutoHDR
        </button>
        <nav className="flex items-center gap-4 text-xs text-neutral-400">
          <span className="hover:text-neutral-100 cursor-default">Contact</span>
          <span className="hover:text-neutral-100 cursor-default">Pricing</span>
          <span className="hover:text-neutral-100 cursor-default">Models</span>
          <span className="text-neutral-100 underline-offset-4">Video Studio</span>
          <span className="hover:text-neutral-100 cursor-default">Listings</span>
        </nav>
      </div>

      <div className="flex items-center gap-2">
        <label className="text-xs text-neutral-500">Project</label>
        <select
          value={selectedProjectSlug ?? ""}
          onChange={(e) => onSelectProject(e.target.value)}
          className="rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-1.5 text-sm text-neutral-100 focus:border-blue-500 focus:outline-none"
        >
          <option value="" disabled>
            {projects.length === 0 ? "No projects" : "Choose a project"}
          </option>
          {projects.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name} ({p.photo_count})
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-3">
        <button
          className="rounded-full border border-neutral-700 bg-neutral-900 px-4 py-1.5 text-sm text-neutral-200 hover:bg-neutral-800"
          disabled
        >
          Export Video
        </button>
        <div className="h-8 w-8 rounded-full bg-gradient-to-br from-amber-400 to-orange-600" />
      </div>
    </header>
  );
}
