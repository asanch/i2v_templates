"""Thin wrapper around fal-client.

Exposes:
  - `upload_local_file(path)` — upload a local file to fal's CDN, return a public URL.
    fal subscribe requires public URLs (or data URIs) for image inputs.
  - `subscribe(model_id, arguments, *, with_logs=False)` — call fal subscribe with
    sane defaults and a single retry on transient errors.

Why a wrapper:
  - fal-client's API surface is tiny but has a few sharp edges (auth via env,
    log streaming yields a generator, return shape is not typed). Centralising
    these makes the rest of the codebase clean.
  - Future swaps (sync polling vs. webhooks, alt providers) live behind this
    surface without touching image_pass.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import fal_client
from dotenv import load_dotenv

# Load .env on first import so callers don't have to.
load_dotenv()


class FalConfigError(RuntimeError):
    pass


class FalCallError(RuntimeError):
    pass


def _ensure_credentials() -> None:
    if not os.environ.get("FAL_KEY"):
        raise FalConfigError(
            "FAL_KEY is not set. Copy .env.example to .env and add your fal key, or "
            "export FAL_KEY in your shell."
        )


def upload_local_file(path: str | Path) -> str:
    """Upload a local file to fal's CDN and return its public URL."""
    _ensure_credentials()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"Expected a file, got a directory: {p}")
    return fal_client.upload_file(str(p))


def subscribe(
    model_id: str,
    arguments: dict[str, Any],
    *,
    with_logs: bool = False,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Call fal subscribe synchronously. Returns the model's result dict.

    Args:
        model_id: fal route, e.g. 'fal-ai/nano-banana/edit'.
        arguments: model-specific input dict (built by a model adapter).
        with_logs: if True, stream logs to stdout while the job runs.
        max_retries: retry transient failures up to this many times.
    """
    _ensure_credentials()

    def _on_queue_update(update: Any) -> None:  # pragma: no cover — print-only
        if with_logs and getattr(update, "logs", None):
            for log in update.logs:
                msg = log.get("message") if isinstance(log, dict) else getattr(log, "message", "")
                if msg:
                    print(f"  [fal] {msg}")

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = fal_client.subscribe(
                model_id,
                arguments=arguments,
                with_logs=with_logs,
                on_queue_update=_on_queue_update if with_logs else None,
            )
            if not isinstance(result, dict):
                raise FalCallError(
                    f"Unexpected fal response type: {type(result).__name__} (expected dict)"
                )
            return result
        except Exception as exc:  # broad on purpose — retry once on anything transient
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                print(
                    f"  [fal] call failed ({type(exc).__name__}: {exc}). "
                    f"Retrying in {wait}s (attempt {attempt + 2}/{max_retries + 1})..."
                )
                time.sleep(wait)
                continue
            break

    raise FalCallError(f"fal subscribe failed after {max_retries + 1} attempts: {last_exc}") from last_exc
