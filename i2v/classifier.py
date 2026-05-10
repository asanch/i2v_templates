"""Photo classifier — assigns user-uploaded photos to template slots.

Three responsibilities:
  1. classify_photos(paths)
        For each photo, vision-LLM call returns scene_kind, hero_score,
        time_of_day, has_window_view, suggested_slot_ids, confidence.
  2. assign_to_slots(classifications, template)
        Greedy: each slot picks the highest-hero_score photo whose
        scene_kind matches. Slots with no candidates are marked inactive.
        Each slot's primary photo is locked from being another slot's primary.
  3. resolve_alternate_angles(plan)
        For each active slot with same-scene candidates remaining, vision-LLM
        compares the primary to each candidate and scores "how plausible is
        this as an end-keyframe for a slow camera move from the primary."
        Best score above threshold (default 0.7) → real_reference end frame.
        Otherwise the slot will need multi_ref_inpaint synthesis.

Plus: each active slot accumulates additional_reference_photo_paths — the
remaining same-scene photos (capped at 3) that will be passed to the image
pass and the inpaint step as architecture anchors.

Routing constraint: every model call goes through fal. The vision LLM
endpoint we use is `openrouter/router/vision` with
`model=google/gemini-2.5-flash`. No direct Google/Anthropic SDKs — fal is
the single integration point.

Output is an AssignmentPlan that the apply pipeline (run_template, future)
consumes verbatim.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from i2v import fal_client
from i2v.types import Template


# ─── Constants & types ───────────────────────────────────────────────────────


# fal's supported vision endpoint with model selection (any-llm/vision is
# deprecated; openrouter/router/vision is the replacement).
VISION_ENDPOINT = "openrouter/router/vision"

# Gemini 2.5 Flash is fast, cheap, vision-capable. Pin to flash for
# classification. We can switch to gemini-2.5-pro for slot-zero verification
# runs if accuracy needs it.
VISION_MODEL = "google/gemini-2.5-flash"

# Vision LLM cost is small but predictable. Cap how much we feed.
MAX_REFERENCE_PHOTOS_PER_SLOT = 3
ALTERNATE_ANGLE_THRESHOLD_DEFAULT = 0.7


SCENE_KINDS_LIST: list[str] = [
    "exterior_front", "exterior_back", "exterior_aerial",
    "entry", "hallway", "stairs",
    "kitchen", "dining", "living", "office", "media_room",
    "primary_bed", "secondary_bed",
    "primary_bath", "secondary_bath",
    "outdoor_yard", "outdoor_pool", "outdoor_patio",
    "detail_architectural", "detail_material", "detail_window",
    "view_window",
]

TimeOfDay = Literal[
    "morning", "midday", "afternoon", "golden", "dusk", "blue_hour",
    "night", "unknown",
]


class PhotoClassification(BaseModel):
    """LLM-returned classification for a single photo."""

    model_config = ConfigDict(extra="forbid")

    photo_path: str
    scene_kind: str = Field(description="One of the known SCENE_KINDS_LIST values.")
    hero_score: float = Field(ge=0.0, le=1.0)
    time_of_day: TimeOfDay = "unknown"
    has_window_view: bool = False
    suggested_slot_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class AlternateAngleScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_photo_path: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class SlotAssignment(BaseModel):
    """Where a slot ended up after classification + alternate-angle resolution."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str
    is_active: bool
    inactive_reason: str | None = None

    primary_photo_path: str | None = None
    primary_classification: PhotoClassification | None = None

    end_frame_strategy: Literal[
        "real_reference", "multi_ref_inpaint", "depthflow_only", "none"
    ] = "none"
    end_frame_photo_path: str | None = None
    end_frame_match_confidence: float | None = None

    additional_reference_photo_paths: list[str] = Field(default_factory=list)
    """Other same-scene photos to pass as architecture anchors during the
    image pass and the inpaint step. Capped at MAX_REFERENCE_PHOTOS_PER_SLOT."""


class AssignmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    photo_classifications: dict[str, PhotoClassification]
    slot_assignments: list[SlotAssignment]
    inactive_slot_ids: list[str]
    unassigned_photo_paths: list[str]


# ─── Vision LLM call (via fal) ───────────────────────────────────────────────


def _strip_markdown_fences(text: str) -> str:
    """LLMs sometimes wrap JSON in ```json fences despite instructions not to."""
    fence = re.compile(r"^```(?:json)?\s*\n(.*?)\n?```\s*$", re.DOTALL)
    m = fence.match(text.strip())
    return m.group(1) if m else text


def _call_vision_llm(
    prompt: str,
    system_prompt: str,
    image_urls: list[str],
    *,
    temperature: float = 0.1,
    with_logs: bool = False,
) -> str:
    """Call fal's vision LLM endpoint. Returns the raw `output` string.

    We parse JSON downstream since the endpoint doesn't expose a
    response_mime_type. The system_prompt enforces 'JSON only, no markdown'.
    """
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "system_prompt": system_prompt,
        "model": VISION_MODEL,
        "image_urls": image_urls,
        "temperature": temperature,
        "priority": "latency",
    }
    response = fal_client.subscribe(VISION_ENDPOINT, arguments, with_logs=with_logs)
    if not isinstance(response, dict):
        raise RuntimeError(
            f"Vision LLM returned non-dict response: {type(response).__name__}"
        )
    if response.get("error"):
        raise RuntimeError(f"Vision LLM error: {response['error']}")
    text = response.get("output")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(
            f"Vision LLM returned empty output. Full response keys: "
            f"{list(response.keys())}"
        )
    return _strip_markdown_fences(text)


def _upload_photos(paths: list[Path]) -> list[str]:
    """Upload a list of local images to fal's CDN and return public URLs."""
    return [fal_client.upload_local_file(p) for p in paths]


# ─── classify_photo ─────────────────────────────────────────────────────────


_CLASSIFY_SYSTEM_PROMPT = (
    "You are tagging real-estate photos for a cinematic walkthrough video "
    "pipeline. For each photo you'll return a strict JSON object identifying "
    "the scene kind, how strong the photo would be as a featured/hero shot, "
    "time-of-day, and whether it has window views.\n\n"
    "Be conservative on hero_score: only photos that are well-composed, "
    "well-lit, and clearly readable as the room they depict should score "
    "above 0.7. Cluttered, poorly framed, or off-axis shots score below 0.5.\n\n"
    "Score time_of_day from natural light cues. If unclear, say 'unknown'.\n\n"
    "For suggested_slot_ids, pick the top 1-3 ids from the candidate list "
    "this photo could fill. If none fit clearly, return an empty list.\n\n"
    "OUTPUT FORMAT: Return ONLY a single JSON object. No markdown fences, no "
    "explanation, no prefix, no suffix. Just the JSON object."
)


def _classify_photo_prompt(slot_ids: list[str]) -> str:
    return (
        f"Available slot ids: {slot_ids}\n\n"
        f"Available scene_kind values: {SCENE_KINDS_LIST}\n\n"
        f"Return strict JSON with these keys:\n"
        f'  "scene_kind": one of the scene_kind values\n'
        f'  "hero_score": float 0-1\n'
        f'  "time_of_day": one of [morning, midday, afternoon, golden, dusk, '
        f'blue_hour, night, unknown]\n'
        f'  "has_window_view": boolean\n'
        f'  "suggested_slot_ids": array of strings, 0-3 entries from the slot list\n'
        f'  "confidence": float 0-1\n'
        f'  "notes": short one-sentence rationale\n'
    )


def classify_photo(
    photo_path: str | Path,
    template: Template,
    *,
    photo_url: str | None = None,
) -> PhotoClassification:
    """Classify a single photo against the template's slot list.

    photo_url, if provided, is used directly (saves an upload). Otherwise
    we upload the local file to fal first.
    """
    p = Path(photo_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Photo not found: {p}")
    slot_ids = [s.id for s in template.slots]
    url = photo_url or fal_client.upload_local_file(p)

    raw = _call_vision_llm(
        prompt=_classify_photo_prompt(slot_ids),
        system_prompt=_CLASSIFY_SYSTEM_PROMPT,
        image_urls=[url],
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Classifier returned non-JSON for {p.name}:\n{raw}"
        ) from e

    # Validate scene_kind into the literal set; if the model returns something
    # outside the list we coerce to "detail_architectural" with low confidence
    # rather than raising.
    if data.get("scene_kind") not in SCENE_KINDS_LIST:
        data["scene_kind"] = "detail_architectural"
        data["confidence"] = min(0.3, float(data.get("confidence", 0.3)))

    data.setdefault("hero_score", 0.5)
    data.setdefault("confidence", 0.5)
    data.setdefault("time_of_day", "unknown")
    data.setdefault("has_window_view", False)
    data.setdefault("suggested_slot_ids", [])
    data.setdefault("notes", "")

    return PhotoClassification(photo_path=str(p), **data)


def classify_photos(
    photo_paths: list[str | Path],
    template: Template,
    *,
    verbose: bool = True,
) -> dict[str, PhotoClassification]:
    """Classify a list of photos. Returns dict keyed by absolute path.

    Uploads each photo to fal once up front so subsequent calls (classify and
    later alternate-angle resolution) reuse the same URL. URLs are stamped
    into a separate cache that resolve_alternate_angles re-reads.
    """
    paths = [Path(p).resolve() for p in photo_paths]
    if verbose:
        print(f"  [classify] uploading {len(paths)} photo(s) to fal...")
    urls = _upload_photos(paths)
    url_by_path: dict[str, str] = dict(zip((str(p) for p in paths), urls))

    out: dict[str, PhotoClassification] = {}
    for idx, (p, url) in enumerate(zip(paths, urls), 1):
        if verbose:
            print(f"  [classify {idx}/{len(paths)}] {p.name}")
        cls = classify_photo(p, template, photo_url=url)
        out[str(p)] = cls
        if verbose:
            print(
                f"    → scene={cls.scene_kind} hero={cls.hero_score:.2f} "
                f"conf={cls.confidence:.2f} tod={cls.time_of_day}"
            )

    # Cache uploaded URLs so resolve_alternate_angles can reuse them without
    # re-uploading the same photos. Module-level cache (dict can't hold
    # attributes).
    _URL_CACHE.update(url_by_path)
    return out


# Module-level cache so resolve_alternate_angles can find URLs for photos
# already uploaded by classify_photos. Keyed by absolute path string.
_URL_CACHE: dict[str, str] = {}


def _get_or_upload(path: Path) -> str:
    key = str(path.resolve())
    if key in _URL_CACHE:
        return _URL_CACHE[key]
    url = fal_client.upload_local_file(path)
    _URL_CACHE[key] = url
    return url


# ─── Slot assignment ─────────────────────────────────────────────────────────


def assign_to_slots(
    classifications: dict[str, PhotoClassification],
    template: Template,
) -> AssignmentPlan:
    """Greedy assignment of photos to template slots.

    Algorithm:
      1. For each slot (in template order):
         a. Find unassigned photos with classification.scene_kind == slot.scene_kind
         b. If none → slot is inactive.
         c. Otherwise pick highest hero_score → primary.
      2. For each active slot, gather remaining same-scene photos as
         additional_reference_photo_paths (capped).
      3. Photos that didn't become primary for any slot AND aren't refs anywhere
         become unassigned.

    The alternate-angle resolution and end-frame-strategy decision happen in
    resolve_alternate_angles, called after this.
    """
    assigned_as_primary: set[str] = set()
    assignments: list[SlotAssignment] = []

    for slot in template.slots:
        target_scene = slot.photo_requirement.scene_kind
        candidates = [
            (path, cls)
            for path, cls in classifications.items()
            if cls.scene_kind == target_scene and path not in assigned_as_primary
        ]
        if not candidates:
            assignments.append(
                SlotAssignment(
                    slot_id=slot.id,
                    is_active=False,
                    inactive_reason=(
                        f"No unassigned photos with scene_kind={target_scene!r}"
                    ),
                )
            )
            continue

        candidates.sort(key=lambda x: (x[1].hero_score, x[1].confidence), reverse=True)
        primary_path, primary_cls = candidates[0]
        assigned_as_primary.add(primary_path)

        ref_pool = [
            (path, cls)
            for path, cls in classifications.items()
            if cls.scene_kind == target_scene and path != primary_path
        ]
        ref_pool.sort(key=lambda x: (x[1].hero_score, x[1].confidence), reverse=True)
        ref_paths = [p for p, _ in ref_pool[:MAX_REFERENCE_PHOTOS_PER_SLOT]]

        assignments.append(
            SlotAssignment(
                slot_id=slot.id,
                is_active=True,
                primary_photo_path=primary_path,
                primary_classification=primary_cls,
                end_frame_strategy="none",
                additional_reference_photo_paths=ref_paths,
            )
        )

    used_paths: set[str] = set(assigned_as_primary)
    for a in assignments:
        used_paths.update(a.additional_reference_photo_paths)
    unassigned = [p for p in classifications if p not in used_paths]

    return AssignmentPlan(
        template_id=template.template.id,
        photo_classifications=classifications,
        slot_assignments=assignments,
        inactive_slot_ids=[a.slot_id for a in assignments if not a.is_active],
        unassigned_photo_paths=unassigned,
    )


# ─── Alternate-angle resolution ──────────────────────────────────────────────


_ALT_ANGLE_SYSTEM_PROMPT = (
    "You score how plausible each candidate photo is as the END keyframe of "
    "a slow cinematic camera move starting from a primary photo. Both photos "
    "depict the same room from (potentially) different angles.\n\n"
    "A high score means: the candidate looks like a natural destination for a "
    "gentle truck/dolly/orbit from the primary — the camera could plausibly "
    "have moved that way without cutting. The two photos share architecture, "
    "materials, and lighting style.\n\n"
    "A low score means: the candidate is unrelated, or the angle change is too "
    "abrupt to read as continuous motion (e.g. one is wide and the other is a "
    "tight macro of an unrelated surface).\n\n"
    "OUTPUT FORMAT: Return ONLY a JSON array of objects. No markdown fences, "
    "no prefix, no suffix. Each object has 'score' (float 0-1) and 'reason' "
    "(short string). The array must be in the same order as the candidates "
    "(image 2 first, image 3 second, etc.)."
)


def _alt_angle_prompt(num_candidates: int) -> str:
    return (
        f"Image 1 is the PRIMARY (start keyframe). The next {num_candidates} "
        f"images are CANDIDATES for the end keyframe. Return a JSON array of "
        f"{num_candidates} objects, in order:\n"
        f'[{{"score": float 0-1, "reason": "..."}}, ...]'
    )


def resolve_alternate_angles(
    plan: AssignmentPlan,
    *,
    threshold: float = ALTERNATE_ANGLE_THRESHOLD_DEFAULT,
    verbose: bool = True,
) -> AssignmentPlan:
    """For each active slot with reference candidates, decide end-frame strategy."""
    updated_assignments: list[SlotAssignment] = []

    for assignment in plan.slot_assignments:
        if not assignment.is_active or not assignment.primary_photo_path:
            updated_assignments.append(assignment)
            continue

        candidates = assignment.additional_reference_photo_paths
        if not candidates:
            assignment_dict = assignment.model_dump()
            assignment_dict["end_frame_strategy"] = "depthflow_only"
            updated_assignments.append(SlotAssignment(**assignment_dict))
            if verbose:
                print(
                    f"  [alt-angle] {assignment.slot_id}: no candidates → depthflow_only"
                )
            continue

        if verbose:
            print(
                f"  [alt-angle] {assignment.slot_id}: scoring "
                f"{len(candidates)} candidate(s)..."
            )

        primary_url = _get_or_upload(Path(assignment.primary_photo_path))
        candidate_urls = [_get_or_upload(Path(c)) for c in candidates]

        try:
            raw = _call_vision_llm(
                prompt=_alt_angle_prompt(len(candidates)),
                system_prompt=_ALT_ANGLE_SYSTEM_PROMPT,
                image_urls=[primary_url, *candidate_urls],
            )
            scores_raw = json.loads(raw)
            if not isinstance(scores_raw, list):
                raise ValueError("Expected JSON array")
        except (RuntimeError, json.JSONDecodeError, ValueError) as e:
            if verbose:
                print(f"    LLM error / unparsable: {e} → multi_ref_inpaint")
            assignment_dict = assignment.model_dump()
            assignment_dict["end_frame_strategy"] = "multi_ref_inpaint"
            updated_assignments.append(SlotAssignment(**assignment_dict))
            continue

        scored: list[AlternateAngleScore] = []
        for cand_path, entry in zip(candidates, scores_raw):
            try:
                scored.append(
                    AlternateAngleScore(
                        candidate_photo_path=cand_path,
                        score=float(entry.get("score", 0.0)),
                        reason=str(entry.get("reason", "")),
                    )
                )
            except (TypeError, ValueError, AttributeError):
                continue

        if not scored:
            assignment_dict = assignment.model_dump()
            assignment_dict["end_frame_strategy"] = "multi_ref_inpaint"
            updated_assignments.append(SlotAssignment(**assignment_dict))
            if verbose:
                print(f"    no parsable scores → multi_ref_inpaint")
            continue

        scored.sort(key=lambda s: s.score, reverse=True)
        best = scored[0]
        if verbose:
            for s in scored:
                print(
                    f"    candidate {Path(s.candidate_photo_path).name}: "
                    f"score={s.score:.2f}  reason={s.reason[:60]}..."
                )

        if best.score >= threshold:
            assignment_dict = assignment.model_dump()
            assignment_dict["end_frame_strategy"] = "real_reference"
            assignment_dict["end_frame_photo_path"] = best.candidate_photo_path
            assignment_dict["end_frame_match_confidence"] = best.score
            assignment_dict["additional_reference_photo_paths"] = [
                p
                for p in assignment.additional_reference_photo_paths
                if p != best.candidate_photo_path
            ]
            updated_assignments.append(SlotAssignment(**assignment_dict))
            if verbose:
                print(
                    f"    → real_reference: {Path(best.candidate_photo_path).name} "
                    f"(score {best.score:.2f})"
                )
        else:
            assignment_dict = assignment.model_dump()
            assignment_dict["end_frame_strategy"] = "multi_ref_inpaint"
            updated_assignments.append(SlotAssignment(**assignment_dict))
            if verbose:
                print(
                    f"    → multi_ref_inpaint (best score {best.score:.2f} "
                    f"below threshold {threshold})"
                )

    return AssignmentPlan(
        template_id=plan.template_id,
        photo_classifications=plan.photo_classifications,
        slot_assignments=updated_assignments,
        inactive_slot_ids=plan.inactive_slot_ids,
        unassigned_photo_paths=plan.unassigned_photo_paths,
    )


# ─── Top-level orchestrator ──────────────────────────────────────────────────


def classify_and_assign(
    photo_paths: list[str | Path],
    template: Template,
    *,
    alternate_angle_threshold: float = ALTERNATE_ANGLE_THRESHOLD_DEFAULT,
    verbose: bool = True,
) -> AssignmentPlan:
    """Full pipeline: classify each photo → assign to slots → resolve end frames."""
    classifications = classify_photos(photo_paths, template, verbose=verbose)
    plan = assign_to_slots(classifications, template)
    plan = resolve_alternate_angles(
        plan, threshold=alternate_angle_threshold, verbose=verbose
    )
    return plan
