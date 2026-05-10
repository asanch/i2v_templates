"use client";

import { useEffect, useRef, useState } from "react";

import { JobRecord, pollJob } from "./api";

const POLL_INTERVAL_MS = 2000;

/**
 * useJob(jobId) — polls GET /jobs/{id} every 2s while the job is in flight.
 * Stops automatically when the job reaches `done` or `error`. Returns the
 * latest job record (or null until the first poll succeeds).
 *
 * Pass `null` to disable polling (e.g. when no job has started for this slot).
 *
 * The hook deliberately does NOT cancel the underlying backend work when the
 * component unmounts — the work is a fire-and-forget task on the server.
 * It just stops polling.
 */
export function useJob(jobId: string | null): {
  job: JobRecord | null;
  error: string | null;
} {
  const [job, setJob] = useState<JobRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    setError(null);
    if (!jobId) {
      setJob(null);
      return;
    }

    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (cancelledRef.current) return;
      try {
        // jobId narrowed to string by the early return above.
        const next = await pollJob(jobId as string);
        if (cancelledRef.current) return;
        setJob(next);
        if (next.status === "done" || next.status === "error") {
          return; // stop polling
        }
        timer = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (e) {
        if (cancelledRef.current) return;
        setError(String(e));
        // Retry on transient errors after a short backoff.
        timer = setTimeout(tick, POLL_INTERVAL_MS * 2);
      }
    }
    tick();

    return () => {
      cancelledRef.current = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  return { job, error };
}
