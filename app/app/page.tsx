"use client";

import { useEffect, useState } from "react";

import {
  Photo,
  Project,
  TemplateFull,
  TemplateSummary,
  fetchPhotos,
  fetchProjects,
  fetchTemplate,
  fetchTemplates,
} from "@/lib/api";
import StudioWorkspace from "./_components/StudioWorkspace";
import TemplateGallery from "./_components/TemplateGallery";
import TopNav from "./_components/TopNav";

/**
 * Studio entry point. Two states:
 *
 *   1. No template selected — show TemplateGallery.
 *   2. Template selected   — show StudioWorkspace (3-pane layout).
 *
 * Project switcher in TopNav. Photos refresh when the project changes.
 *
 * No persistence yet; refresh resets state. Adding a small URL query param
 * for the selected template+project would survive reloads — easy follow-up.
 */
export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedProjectSlug, setSelectedProjectSlug] = useState<string | null>(null);
  const [photos, setPhotos] = useState<Photo[]>([]);

  const [selectedTemplateSummary, setSelectedTemplateSummary] =
    useState<TemplateSummary | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateFull | null>(null);

  // Load projects + templates on mount.
  // We deliberately do NOT auto-select a project. The gallery view should
  // start blank; project association happens by clicking a Project tile or
  // by picking a project from the switcher inside the studio view.
  useEffect(() => {
    Promise.all([fetchProjects(), fetchTemplates()])
      .then(([p, t]) => {
        setProjects(p);
        setTemplates(t);
      })
      .catch((err) => setLoadError(String(err)));
    // We intentionally only run this once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When the project changes, refresh photos.
  useEffect(() => {
    if (!selectedProjectSlug) {
      setPhotos([]);
      return;
    }
    let cancelled = false;
    fetchPhotos(selectedProjectSlug)
      .then((p) => {
        if (!cancelled) setPhotos(p);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProjectSlug]);

  const selectedProject =
    projects.find((p) => p.slug === selectedProjectSlug) ?? null;

  /**
   * Clicking a Style tile starts a fresh project — no images yet. Clear any
   * previously-selected project so the studio opens with an empty photo
   * panel and prompts the user to add images.
   */
  const handleSelectTemplate = (t: TemplateSummary) => {
    setSelectedProjectSlug(null);
    setSelectedTemplateSummary(t);
    fetchTemplate(t.id)
      .then(setSelectedTemplate)
      .catch((err) => setLoadError(String(err)));
  };

  /**
   * A project tile click opens the studio with both the project AND the
   * template that project was created with. Look up the template summary
   * (already loaded) by id; fetch the full template body.
   */
  const handleSelectProject = (project: Project) => {
    setSelectedProjectSlug(project.slug);
    const summary = templates.find((t) => t.id === project.template_id);
    if (!summary) {
      setLoadError(
        `Project ${project.name} references template ${project.template_id} but that template is missing from /templates response.`
      );
      return;
    }
    setSelectedTemplateSummary(summary);
    fetchTemplate(summary.id)
      .then(setSelectedTemplate)
      .catch((err) => setLoadError(String(err)));
  };

  const handleResetTemplate = () => {
    setSelectedTemplate(null);
    setSelectedTemplateSummary(null);
  };

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-neutral-950 px-6 text-center">
        <div className="max-w-md space-y-3 rounded-xl border border-red-900 bg-red-950/40 p-6">
          <h2 className="text-lg font-semibold text-red-200">
            Backend not reachable
          </h2>
          <p className="text-sm text-red-300">
            Could not load from {process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000"}.
          </p>
          <p className="text-xs text-red-400">{loadError}</p>
          <p className="pt-2 text-xs text-neutral-400">
            Start the FastAPI backend with{" "}
            <code className="rounded bg-neutral-800 px-1.5 py-0.5">make api-dev</code>{" "}
            from the repo root.
          </p>
        </div>
      </div>
    );
  }

  // Fixed-height shell. Top nav is shrink-0, content fills remaining viewport.
  // The studio view depends on this for its no-scroll layout — children can
  // safely use `flex-1 min-h-0` to claim a finite height.
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-neutral-950 text-neutral-100">
      <TopNav
        onResetTemplate={handleResetTemplate}
        showExport={!!selectedTemplate}
      />

      {!selectedTemplate ? (
        <TemplateGallery
          projects={projects}
          templates={templates}
          onSelectProject={handleSelectProject}
          onSelectTemplate={handleSelectTemplate}
        />
      ) : (
        <StudioWorkspace
          template={selectedTemplate}
          templateSummary={selectedTemplateSummary!}
          photos={photos}
          selectedProjectName={selectedProject?.name ?? null}
          selectedProjectSlug={selectedProjectSlug}
          onBackToProjects={handleResetTemplate}
        />
      )}
    </div>
  );
}
