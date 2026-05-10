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
  useEffect(() => {
    Promise.all([fetchProjects(), fetchTemplates()])
      .then(([p, t]) => {
        setProjects(p);
        setTemplates(t);
        if (p.length > 0 && !selectedProjectSlug) {
          setSelectedProjectSlug(p[0].slug);
        }
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

  const handleSelectTemplate = (t: TemplateSummary) => {
    setSelectedTemplateSummary(t);
    fetchTemplate(t.id)
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

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <TopNav
        projects={projects}
        selectedProjectSlug={selectedProjectSlug}
        onSelectProject={setSelectedProjectSlug}
        onResetTemplate={handleResetTemplate}
      />

      {!selectedTemplate ? (
        <TemplateGallery
          templates={templates}
          onSelect={handleSelectTemplate}
        />
      ) : (
        <StudioWorkspace
          template={selectedTemplate}
          templateSummary={selectedTemplateSummary!}
          photos={photos}
          selectedProjectName={selectedProject?.name ?? null}
        />
      )}
    </div>
  );
}
