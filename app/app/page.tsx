import HealthCheck from "./_components/HealthCheck";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-16">
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-widest text-neutral-500">
          i2v_templates
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">
          Photo-to-video pipeline
        </h1>
        <p className="text-neutral-600 dark:text-neutral-400">
          Apply a creative template (image prompts, camera-motion prompts, music)
          to a set of amateur real-estate photos and produce a professional
          walkthrough. UX TBD.
        </p>
      </header>

      <section className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-4">
        <h2 className="text-sm font-medium mb-2">Status</h2>
        <HealthCheck />
      </section>

      <section className="text-sm text-neutral-600 dark:text-neutral-400 space-y-1">
        <p>
          Backend URL: <code className="font-mono text-xs">{process.env.NEXT_PUBLIC_BACKEND_URL || "(not set)"}</code>
        </p>
        <p>
          Set <code className="font-mono text-xs">NEXT_PUBLIC_BACKEND_URL</code> in{" "}
          <code className="font-mono text-xs">.env.local</code> when the FastAPI
          wrapper is ready.
        </p>
      </section>
    </main>
  );
}
