"use client";

import { useEffect, useState } from "react";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; ts: string }
  | { kind: "error"; message: string };

export default function HealthCheck() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data?.ok) {
          setState({ kind: "ok", ts: data.ts });
        } else {
          setState({ kind: "error", message: "unexpected response" });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({ kind: "error", message: String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return <p className="text-sm text-neutral-500">Pinging /api/health…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="text-sm text-red-600 dark:text-red-400">
        Health check failed: {state.message}
      </p>
    );
  }
  return (
    <p className="text-sm text-emerald-600 dark:text-emerald-400">
      Next.js is alive at {state.ts}
    </p>
  );
}
