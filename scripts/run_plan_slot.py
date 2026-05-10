"""run_plan_slot — run a single slot end-to-end from an AssignmentPlan.

Inputs:
  • A plan JSON produced by classify_and_assign
  • A template JSON (so we know each slot's image_pipeline / video_pass spec)
  • A slot id to run

Steps for that slot (using the plan's assignment for it):
  1. Image pass on the primary photo, with the plan's references as anchors.
  2. End-frame synthesis based on plan.end_frame_strategy:
       real_reference   → run image pass on the alternate photo too
       multi_ref_inpaint → depthflow extreme + edge inpaint
       depthflow_only   → no end frame
       none             → fallback, treat as depthflow_only
  3. Video pass:
       If we have an end frame → switch model to Kling 2.6 Pro and use it
                                  as tail_image_url.
       Else → use the slot's declared video model (typically a depthflow
              preset — small motion, no end frame).

All artifacts land under outputs/<house-slug>/<run-id>/<slot-id>/.

Example:

    python -m scripts.run_plan_slot \\
        --plan outputs/thatcher/assignments/2026-05-09T18-00-00_plan.json \\
        --template templates/cinematic-editorial-v1.json \\
        --slot 03_kitchen_wide_truck
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from i2v.classifier import AssignmentPlan, SlotAssignment
from i2v.end_frame import (
    synthesize_end_frame_multi_ref_inpaint,
    synthesize_end_frame_real_reference,
)
from i2v.image_pass import run_image_pipeline
from i2v.types import Slot, get_slot, load_template
from i2v.video_pass import run_video_pass


# When the plan provides an end frame, switch the video model to one that
# accepts tail_image_url and can do real generative motion. Default to
# Kling 3.0 Pro — meaningfully better motion smoothness and identity
# preservation than 2.6 for architectural shots. Override per-run via
# --generative-video-model. Other defensible choices: Seedance 2.0 i2v
# (`fal-ai/bytedance/seedance/v2.0/image-to-video`).
DEFAULT_GENERATIVE_VIDEO_MODEL = "fal-ai/kling-video/v3/pro/image-to-video"


# When we switch to a generative video model, the slot's default video_pass
# prompt is wrong for the new context — slots ship depthflow-flavored
# prompts ("architecture-locked subtle parallax", "depth-reprojected", "no
# inventing geometry") because their default backend IS depthflow. If we
# send those words to Kling/Seedance the model interprets them as "do
# almost nothing" and ignores the keyframes' worth of motion intent.
#
# The fix: when overriding the model to a generative one, also override
# the prompt with language that asks for actual keyframe interpolation.
# Photorealism + architecture-preservation language is preserved; the
# instruction shifts from "stay still" to "move smoothly between these
# two keyframes."
GENERATIVE_KEYFRAME_PROMPT_TEMPLATE = (
    "Smooth cinematic camera move interpolating naturally between two keyframes "
    "of the same {scene_label}. The motion should feel like a real camera "
    "tracking from the start view to the end view — not a cut, not a "
    "morph. Preserve all architectural details, materials, fixtures, and "
    "lighting from both keyframes exactly. No new windows, doors, or "
    "fixtures appearing in the interpolated frames. Photorealistic editorial "
    "film style, neutral white balance, no abrupt transitions."
)


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "house"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _load_plan(plan_path: Path) -> AssignmentPlan:
    raw = json.loads(plan_path.read_text())
    return AssignmentPlan.model_validate(raw)


def _find_assignment(plan: AssignmentPlan, slot_id: str) -> SlotAssignment:
    for a in plan.slot_assignments:
        if a.slot_id == slot_id:
            return a
    available = ", ".join(s.slot_id for s in plan.slot_assignments)
    raise click.UsageError(
        f"Slot '{slot_id}' not in plan. Available: {available}"
    )


def _derive_house_slug(plan_path: Path, plan: AssignmentPlan) -> str:
    """Best-effort: pick the house slug from the plan path (outputs/<slug>/...).
    Falls back to the template id."""
    parts = plan_path.resolve().parts
    if "outputs" in parts:
        i = parts.index("outputs")
        if i + 1 < len(parts):
            return parts[i + 1]
    return _slugify(plan.template_id)


@click.command(help=__doc__)
@click.option(
    "--plan",
    "plan_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to an AssignmentPlan JSON (produced by classify_and_assign).",
)
@click.option(
    "--template",
    "template_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Template JSON. Must match the plan's template_id.",
)
@click.option(
    "--slot",
    "slot_id",
    required=True,
    type=str,
    help="Slot id to run, e.g. '03_kitchen_wide_truck'.",
)
@click.option(
    "--output-root",
    default=None,
    type=click.Path(file_okay=False),
    help="Output root. Default: outputs/<house-slug>/<run-id>/<slot-id>/",
)
@click.option(
    "--generative-video-model",
    default=DEFAULT_GENERATIVE_VIDEO_MODEL,
    show_default=True,
    type=str,
    help="Video model used when the plan provides an end frame "
    "(real_reference or multi_ref_inpaint). Should support tail_image_url.",
)
@click.option(
    "--video-duration",
    default=6,
    show_default=True,
    type=int,
    help="Duration of the generated clip when using a generative video model.",
)
@click.option(
    "--depthflow-intensity",
    default=None,
    type=float,
    help="Override depthflow intensity (used only when end_frame_strategy=depthflow_only).",
)
@click.option(
    "--end-frame-motion",
    default="horizontal",
    show_default=True,
    type=click.Choice(["horizontal", "vertical", "zoom", "circle"]),
    help="Motion axis for multi_ref_inpaint synthesis. 'horizontal' = lateral truck.",
)
@click.option(
    "--end-frame-intensity",
    default=1.5,
    show_default=True,
    type=float,
    help="DepthFlow intensity for multi_ref_inpaint end-frame synthesis. "
    "Higher = bigger displacement = more edge to inpaint.",
)
@click.option(
    "--end-frame-border-pct",
    default=0.15,
    show_default=True,
    type=float,
    help="Fraction of frame width/height that becomes inpaint mask. "
    "0.15 = outer 15%% gets generated, inner 85%% preserved exactly.",
)
@click.option(
    "--force-strategy",
    default=None,
    type=click.Choice(
        ["real_reference", "multi_ref_inpaint", "depthflow_only"],
        case_sensitive=False,
    ),
    help="Override the plan's end_frame_strategy for this run. Useful for "
    "testing multi_ref_inpaint when the plan happened to resolve to "
    "real_reference, or for forcing depthflow_only to skip the end frame "
    "entirely. NB: forcing real_reference requires the plan to already have "
    "an end_frame_photo_path; forcing multi_ref_inpaint works on any slot.",
)
@click.option("--with-logs/--no-logs", default=True)
def main(
    plan_path: str,
    template_path: str,
    slot_id: str,
    output_root: str | None,
    generative_video_model: str,
    video_duration: int,
    depthflow_intensity: float | None,
    end_frame_motion: str,
    end_frame_intensity: float,
    end_frame_border_pct: float,
    force_strategy: str | None,
    with_logs: bool,
) -> None:
    plan = _load_plan(Path(plan_path))
    template = load_template(template_path)
    if template.template.id != plan.template_id:
        click.secho(
            f"Warning: template id ({template.template.id}) doesn't match "
            f"plan's template_id ({plan.template_id}). Continuing anyway.",
            fg="yellow",
        )
    slot = get_slot(template, slot_id)
    assignment = _find_assignment(plan, slot_id)

    if not assignment.is_active:
        click.secho(
            f"Slot '{slot_id}' is INACTIVE in this plan: {assignment.inactive_reason}",
            fg="red",
        )
        sys.exit(1)

    # Apply --force-strategy override if set. Useful for testing the
    # multi_ref_inpaint path on slots whose plan resolved to real_reference.
    if force_strategy:
        original = assignment.end_frame_strategy
        if force_strategy == "real_reference" and not assignment.end_frame_photo_path:
            click.secho(
                "Cannot force real_reference: this plan has no end_frame_photo_path "
                "for this slot. Re-run classify_and_assign or pick a different slot.",
                fg="red",
            )
            sys.exit(2)
        assignment = assignment.model_copy(update={"end_frame_strategy": force_strategy})
        click.secho(
            f"FORCING strategy: {original} → {force_strategy}",
            fg="yellow",
        )

    house_slug = _derive_house_slug(Path(plan_path), plan)
    run_id = f"{_timestamp()}_{slot_id}_{uuid.uuid4().hex[:6]}"
    if output_root:
        out_dir = Path(output_root) / run_id
    else:
        out_dir = Path("outputs") / house_slug / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    click.echo(
        f"Plan:       {plan_path}\n"
        f"Template:   {template.template.id}\n"
        f"Slot:       {slot.id} — {slot.label}\n"
        f"House:      {house_slug}\n"
        f"Strategy:   {assignment.end_frame_strategy}\n"
        f"Primary:    {Path(assignment.primary_photo_path).name}\n"
        f"Refs:       {[Path(p).name for p in assignment.additional_reference_photo_paths]}\n"
        f"End frame:  {Path(assignment.end_frame_photo_path).name if assignment.end_frame_photo_path else '(synthesized or none)'}\n"
        f"Output dir: {out_dir}\n"
    )

    # ─── Step 1: Image pipeline on primary ─────────────────────────────────
    click.echo("\n--- STEP 1: image pass on primary ---")
    primary_dir = out_dir / "primary"
    primary_result = run_image_pipeline(
        slot=slot,
        input_image_path=assignment.primary_photo_path,
        output_root=str(primary_dir),
        template_id=plan.template_id,
        with_logs=with_logs,
        reference_photos_override=assignment.additional_reference_photo_paths,
    )
    start_frame_path = primary_result.final_output_path
    click.echo(f"  primary final → {start_frame_path}")

    # ─── Step 2: End frame synthesis ───────────────────────────────────────
    end_frame_path: str | None = None
    end_frame_meta: dict | None = None
    strategy = assignment.end_frame_strategy

    click.echo(f"\n--- STEP 2: end frame ({strategy}) ---")
    if strategy == "real_reference":
        if not assignment.end_frame_photo_path:
            click.secho(
                "Plan says real_reference but no end_frame_photo_path. "
                "Falling back to depthflow_only.",
                fg="yellow",
            )
        else:
            end_dir = out_dir / "end_frame_real"
            end_frame_meta = synthesize_end_frame_real_reference(
                end_frame_photo_path=assignment.end_frame_photo_path,
                slot=slot,
                output_dir=end_dir,
                additional_reference_paths=assignment.additional_reference_photo_paths,
                with_logs=with_logs,
            )
            end_frame_path = end_frame_meta["final_end_frame_path"]
            click.echo(f"  real_reference end → {end_frame_path}")
    elif strategy == "multi_ref_inpaint":
        end_dir = out_dir / "end_frame_inpaint"
        end_dir.mkdir(parents=True, exist_ok=True)
        primary_notes = (
            assignment.primary_classification.notes
            if assignment.primary_classification
            else ""
        )
        end_frame_meta = synthesize_end_frame_multi_ref_inpaint(
            start_frame_path=start_frame_path,
            output_dir=end_dir,
            motion=end_frame_motion,
            intensity=end_frame_intensity,
            border_pct=end_frame_border_pct,
            slot_label=slot.label,
            primary_notes=primary_notes,
            additional_reference_paths=assignment.additional_reference_photo_paths,
            with_logs=with_logs,
        )
        end_frame_path = end_frame_meta["final_end_frame_path"]
        click.echo(f"  multi_ref_inpaint end → {end_frame_path}")
    elif strategy in ("depthflow_only", "none"):
        click.echo(f"  no end frame; video pass will use depthflow alone")
    else:
        click.secho(f"  unknown strategy {strategy!r}; treating as depthflow_only", fg="yellow")

    # ─── Step 3: Video pass ────────────────────────────────────────────────
    click.echo("\n--- STEP 3: video pass ---")
    video_dir = out_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    if end_frame_path:
        # We have a real start AND end frame — use a generative i2v model
        # that accepts tail_image_url. Override the slot's default model
        # AND the slot's prompt: the default prompt is depthflow-flavored
        # ("architecture-locked subtle parallax") which makes Kling/Seedance
        # produce minimal motion. We replace it with a keyframe-interpolation
        # prompt that asks for real camera movement between the two frames.
        keyframe_prompt = GENERATIVE_KEYFRAME_PROMPT_TEMPLATE.format(
            scene_label=slot.label.lower()
        )
        modified_spec = slot.video_pass.model_copy(update={"prompt": keyframe_prompt})
        click.echo(
            f"  end frame present → switching video model to "
            f"{generative_video_model}"
        )
        click.echo(f"  switching prompt to keyframe-interpolation form")
        video_result = run_video_pass(
            input_image_path=start_frame_path,
            video_pass_spec=modified_spec,
            output_dir=video_dir,
            slot_id=slot.id,
            template_id=plan.template_id,
            run_id=run_id,
            end_frame_image_path=end_frame_path,
            with_logs=with_logs,
            model_override=generative_video_model,
            duration_override=video_duration,
            overscan_pct=0.0,  # generative i2v doesn't need depthflow overscan
        )
    else:
        # No end frame — depthflow camera move via the slot's declared model.
        click.echo(f"  no end frame → using slot model: {slot.video_pass.model}")
        video_result = run_video_pass(
            input_image_path=start_frame_path,
            video_pass_spec=slot.video_pass,
            output_dir=video_dir,
            slot_id=slot.id,
            template_id=plan.template_id,
            run_id=run_id,
            with_logs=with_logs,
            duration_override=video_duration,
            intensity_override=depthflow_intensity,
        )

    # ─── Summary ───────────────────────────────────────────────────────────
    click.echo("\n=== DONE ===")
    click.echo(f"  run_id:       {run_id}")
    click.echo(f"  output dir:   {out_dir}")
    click.echo(f"  start frame:  {start_frame_path}")
    if end_frame_path:
        click.echo(f"  end frame:    {end_frame_path}")
    click.echo(f"  video clip:   {video_result.output_path}")

    summary = {
        "run_id": run_id,
        "house_slug": house_slug,
        "template_id": plan.template_id,
        "slot_id": slot.id,
        "plan_path": str(Path(plan_path).resolve()),
        "strategy": strategy,
        "primary_photo_path": assignment.primary_photo_path,
        "references": assignment.additional_reference_photo_paths,
        "start_frame_path": start_frame_path,
        "end_frame_path": end_frame_path,
        "end_frame_meta": end_frame_meta,
        "video_clip_path": video_result.output_path,
        "video_model": video_result.model,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
