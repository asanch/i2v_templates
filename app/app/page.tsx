"use client";

import { useEffect, useRef, useState } from "react";

import {
  Photo,
  Project,
  TemplateFull,
  TemplateSummary,
  classifyProject,
  createProject,
  deleteProject,
  fetchPhotos,
  fetchProjects,
  fetchTemplate,
  fetchTemplates,
  pollJob,
  runTemplate,
  uploadPhotos,
} from "@/lib/api";
import StudioWorkspace from "./_components/StudioWorkspace";
import TemplateGallery from "./_components/TemplateGallery";
import TopNav from "./_components/TopNav";

/** Poll a job until it terminates (status === "done" or "error"). Resolves
 *  on done; throws on error. Used by the create-by-style orchestrator to
 *  serialize classify → walkthrough kickoff. */
async function pollUntilDone(jobId: string, intervalMs: number = 2000): Promise<void> {
  while (true) {
    const job = await pollJob(jobId);
    if (job.status === "done") return;
    if (job.status === "error") throw new Error(job.error ?? "Job failed");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

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

  // When the project changes, refresh photos. We bump a counter to also
  // re-fetch on demand (after upload completes).
  const [photosTick, setPhotosTick] = useState(0);
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
  }, [selectedProjectSlug, photosTick]);

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

  /** Permanently delete a project. Confirms with the user, calls the
   *  backend, then refreshes the projects list. Also clears any
   *  processing state for that slug so a stale spinner doesn't linger. */
  const handleDeleteProject = async (project: Project): Promise<void> => {
    const ok = window.confirm(
      `Delete project "${project.name}"?\n\nThis removes all uploaded photos, every rendered slot, and every walkthrough export for this project. This cannot be undone.`,
    );
    if (!ok) return;
    try {
      await deleteProject(project.slug);
      const refreshed = await fetchProjects();
      setProjects(refreshed);
      setProcessingProjects((prev) => {
        if (!(project.slug in prev)) return prev;
        const next = { ...prev };
        delete next[project.slug];
        return next;
      });
    } catch (err) {
      setLoadError(String(err));
    }
  };

  // Imperative bridge from TopNav's Export Video button to the studio's
  // run-template handler. StudioWorkspace populates `current` on mount and
  // clears it on unmount so we don't fire stale closures.
  const exportTriggerRef = useRef<(() => void) | null>(null);
  const [studioCanExport, setStudioCanExport] = useState(false);

  /**
   * Per-project processing state for the auto-pipeline kicked off from the
   * Create-by-Style flow. Tracks where each project is in the
   *   classify → walkthrough render
   * pipeline so the gallery tile can show a spinner + "Classifying…" /
   * "Generating walkthrough…" label, and so we can hand the in-flight
   * walkthrough job id to the studio when the user clicks the tile.
   */
  type ProcessingState = {
    phase: "classifying" | "walkthrough" | "done" | "error";
    classifyJobId: string | null;
    templateJobId: string | null;
    error?: string;
  };
  const [processingProjects, setProcessingProjects] = useState<
    Record<string, ProcessingState>
  >({});

  const updateProcessing = (slug: string, patch: Partial<ProcessingState>) => {
    setProcessingProjects((prev) => ({
      ...prev,
      [slug]: { ...(prev[slug] ?? { phase: "classifying", classifyJobId: null, templateJobId: null }), ...patch },
    }));
  };

  /**
   * Drives the full Create-by-Style pipeline end-to-end. The user picks
   * photos + a template in the gallery; on click we:
   *   1. POST /projects to allocate the slug + meta.json
   *   2. POST /projects/{slug}/photos with their files
   *   3. Refresh /projects so the new tile appears in the Projects section
   *      with its cover photo
   *   4. POST /jobs/classify and poll to completion (tile is non-clickable
   *      until this finishes)
   *   5. POST /jobs/run-template to render the entire walkthrough; remember
   *      the jobId so when the user clicks the tile later, the studio can
   *      pick up the in-flight job and show its progress
   *
   * Resolves with the new project slug. Errors are recorded in
   * processingProjects so the gallery tile can render an error state.
   */
  const handleCreateFromStyle = async (
    name: string,
    template: TemplateSummary,
    files: File[],
  ): Promise<string> => {
    // 1. Create the folder
    const created = await createProject(name, template.id);
    const slug = created.slug;
    updateProcessing(slug, { phase: "classifying" });

    try {
      // 2. Upload photos
      await uploadPhotos(slug, files);

      // 3. Refresh projects so the tile shows up immediately with cover
      const refreshed = await fetchProjects();
      setProjects(refreshed);

      // 4. Classify and poll
      const classifyJob = await classifyProject(slug, template.id);
      updateProcessing(slug, { classifyJobId: classifyJob.id });
      await pollUntilDone(classifyJob.id);

      // 5. Kick off full walkthrough — don't wait for completion. The user
      //    can enter the studio at any point and watch progress; we just
      //    remember the jobId so we can hand it down when they click in.
      updateProcessing(slug, { phase: "walkthrough" });
      const tmplJob = await runTemplate({
        project_slug: slug,
        template_id: template.id,
      });
      updateProcessing(slug, { templateJobId: tmplJob.id });

      // Background-poll the walkthrough so we know when to mark it done
      // (used to clear the spinner overlay on the tile).
      pollUntilDone(tmplJob.id)
        .then(() => updateProcessing(slug, { phase: "done" }))
        .catch((err) =>
          updateProcessing(slug, { phase: "error", error: String(err) }),
        );

      return slug;
    } catch (err) {
      updateProcessing(slug, { phase: "error", error: String(err) });
      throw err;
    }
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
        onExportVideo={() => exportTriggerRef.current?.()}
        canExport={studioCanExport}
      />

      {!selectedTemplate ? (
        <TemplateGallery
          projects={projects}
          templates={templates}
          processingProjects={processingProjects}
          onSelectProject={handleSelectProject}
          onSelectTemplate={handleSelectTemplate}
          onCreateFromStyle={handleCreateFromStyle}
          onDeleteProject={handleDeleteProject}
        />
      ) : (
        <StudioWorkspace
          template={selectedTemplate}
          templateSummary={selectedTemplateSummary!}
          photos={photos}
          selectedProjectName={selectedProject?.name ?? null}
          selectedProjectSlug={selectedProjectSlug}
          onBackToProjects={handleResetTemplate}
          onPhotosChanged={() => setPhotosTick((n) => n + 1)}
          exportTriggerRef={exportTriggerRef}
          setCanExport={setStudioCanExport}
          // If this project was created via the auto-pipeline and its
          // walkthrough job is still in flight, hand the jobId down so
          // the studio shows its progress instead of starting cold.
          initialTemplateJobId={
            (selectedProjectSlug
              ? processingProjects[selectedProjectSlug]?.templateJobId
              : null) ?? null
          }
          // After a new project is created via the upload flow, refresh the
          // projects list and switch the active slug to the new project.
          onProjectCreated={(newSlug) => {
            fetchProjects()
              .then((p) => {
                setProjects(p);
                setSelectedProjectSlug(newSlug);
              })
              .catch((err) => setLoadError(String(err)));
          }}
        />
      )}
    </div>
  );
}
