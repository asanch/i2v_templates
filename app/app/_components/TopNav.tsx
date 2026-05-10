"use client";

type Props = {
  onResetTemplate: () => void;
  showExport: boolean;
};

/**
 * Top nav. Brand on the left, secondary nav in the middle, actions on the
 * right. The project switcher and project label live INSIDE the studio
 * workspace, not here — keeps the nav clean and lets the user scan straight
 * to the project name above the player.
 */
export default function TopNav({ onResetTemplate, showExport }: Props) {
  return (
    <header className="flex shrink-0 items-center justify-between border-b border-neutral-800 bg-neutral-950 px-6 py-3">
      <div className="flex items-center gap-6">
        <button
          onClick={onResetTemplate}
          className="flex items-center gap-2 text-sm font-semibold tracking-tight"
          title="Back to all projects"
        >
          <span className="inline-block h-6 w-6 rounded-md bg-gradient-to-br from-blue-500 to-sky-700" />
          AutoHDR
        </button>
        {/* Hide secondary nav at narrow widths so the brand + actions still
            fit. The grid below also collapses gracefully. */}
        <nav className="hidden items-center gap-4 text-xs text-neutral-400 lg:flex">
          <span className="hover:text-neutral-100 cursor-default">Contact</span>
          <span className="hover:text-neutral-100 cursor-default">Pricing</span>
          <span className="hover:text-neutral-100 cursor-default">Models</span>
          <span className="text-neutral-100 underline-offset-4">Video Studio</span>
          <span className="hover:text-neutral-100 cursor-default">Listings</span>
        </nav>
      </div>

      <div className="flex items-center gap-3">
        {showExport && (
          <button
            className="rounded-full border border-neutral-700 bg-neutral-900 px-4 py-1.5 text-sm text-neutral-200 hover:bg-neutral-800"
            disabled
          >
            Export Video
          </button>
        )}
        <div className="h-8 w-8 shrink-0 rounded-full bg-gradient-to-br from-amber-400 to-orange-600" />
      </div>
    </header>
  );
}
