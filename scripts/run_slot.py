"""run_slot — apply one slot's pipeline (image + optionally video) to one photo.

Image pipeline only (default):

    python -m scripts.run_slot \\
        --template templates/cinematic-editorial-v1.json \\
        --slot 03_kitchen_wide_truck \\
        --input inputs/aaron-kitchen.jpg

Image pipeline + video pass in one go:

    python -m scripts.run_slot \\
        --template templates/cinematic-editorial-v1.json \\
        --slot 03_kitchen_wide_truck \\
        --input inputs/aaron-kitchen.jpg \\
        --include-video

To run only the video pass against an existing image, use `scripts/run_video.py`.
"""

from __future__ import annotations

from pathlib import Path

import click

from i2v.image_pass import run_image_pipeline
from i2v.types import get_slot, load_template
from i2v.video_pass import run_video_pass


@click.command(help=__doc__)
@click.option(
    "--template",
    "template_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the template JSON.",
)
@click.option(
    "--slot",
    "slot_id",
    required=True,
    type=str,
    help="Id of the slot to run, e.g. '03_kitchen_wide_truck'.",
)
@click.option(
    "--input",
    "input_image_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Local path to the PRIMARY input photo (the edit target / first frame).",
)
@click.option(
    "--references",
    "references_csv",
    default=None,
    type=str,
    help="Comma-separated paths to ADDITIONAL reference photos of the same scene "
    "from different angles. Used as architecture anchors only — not edited. "
    "Capped at the pass's max_references (default 4). "
    "Example: --references inputs/IMG_5316.JPG,inputs/IMG_5290.JPG",
)
@click.option(
    "--output-root",
    default="outputs",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Output root; a per-run subdir is created.",
)
@click.option(
    "--override-model",
    default=None,
    type=str,
    help="If set, every IMAGE pass uses this model id instead of the one in the template. "
    "Useful for A/B'ing image models without editing the template.",
)
@click.option(
    "--include-video/--no-video",
    default=False,
    help="If set, after the image pipeline finishes, also run the slot's video_pass "
    "on the final frame.",
)
@click.option(
    "--override-video-model",
    default=None,
    type=str,
    help="When using --include-video, override the slot's video model.",
)
@click.option(
    "--override-video-duration",
    default=None,
    type=int,
    help="When using --include-video, override the slot's clip duration (clamped to model limits).",
)
@click.option(
    "--override-video-intensity",
    default=None,
    type=float,
    help="DepthFlow only: override the preset's intensity (range 0-4). "
    "Higher = camera moves further; for faster motion combine with shorter duration.",
)
@click.option(
    "--override-video-steady",
    default=None,
    type=float,
    help="DepthFlow only: override the --steady anchor (-1..2). Lower (0 to -0.3) "
    "anchors foreground; higher anchors background.",
)
@click.option(
    "--video-overscan",
    "video_overscan_pct",
    default=0.10,
    show_default=True,
    type=float,
    help="DepthFlow only: fraction larger to render before center-cropping. "
    "0.10 hides edge stretching with no quality loss. Set 0 to disable.",
)
@click.option(
    "--with-logs/--no-logs",
    default=True,
    help="Stream fal logs to stdout while passes run.",
)
def main(
    template_path: str,
    slot_id: str,
    input_image_path: str,
    references_csv: str | None,
    output_root: str,
    override_model: str | None,
    include_video: bool,
    override_video_model: str | None,
    override_video_duration: int | None,
    override_video_intensity: float | None,
    override_video_steady: float | None,
    video_overscan_pct: float,
    with_logs: bool,
) -> None:
    template = load_template(template_path)
    slot = get_slot(template, slot_id)

    # Parse --references CSV. Empty/whitespace entries are dropped.
    references: list[str] | None = None
    if references_csv:
        references = [p.strip() for p in references_csv.split(",") if p.strip()]

    click.echo(
        f"Template:   {template.template.id} ({template.template.name})\n"
        f"Slot:       {slot.id} — {slot.label}\n"
        f"Image:      {len(slot.image_pipeline)} pass(es)\n"
        f"Video:      {'yes (' + slot.video_pass.model + ')' if include_video and slot.video_pass else 'skipped'}\n"
        f"Primary:    {input_image_path}\n"
        f"References: {references or '(none — single-image mode)'}\n"
    )
    if override_model:
        click.secho(f"Image-model override: every pass will use {override_model}", fg="yellow")

    image_result = run_image_pipeline(
        slot=slot,
        input_image_path=input_image_path,
        output_root=output_root,
        template_id=template.template.id,
        with_logs=with_logs,
        model_override=override_model,
        reference_photos_override=references,
    )

    click.echo("\nIMAGE PIPELINE DONE")
    click.echo(f"  run_id:      {image_result.run_id}")
    click.echo(f"  output_dir:  {image_result.output_dir}")
    for p in image_result.passes:
        click.echo(
            f"  pass {p.pass_index} ({p.pass_label}): "
            f"{Path(p.output_path).name}  ({p.duration_sec}s)"
        )
    click.echo(f"  final →      {image_result.final_output_path}")

    if not include_video:
        click.echo(f"  metadata →   {Path(image_result.output_dir) / 'metadata.json'}")
        return

    if slot.video_pass is None:
        click.secho(
            f"\nNo video_pass declared on slot '{slot.id}'; skipping video.",
            fg="yellow",
        )
        return

    click.echo("\n--- Running video pass ---\n")
    video_result = run_video_pass(
        input_image_path=image_result.final_output_path,
        video_pass_spec=slot.video_pass,
        # Write the mp4 alongside the image-pipeline outputs so a slot's
        # entire artifact set lives in one directory.
        output_dir=image_result.output_dir,
        slot_id=slot.id,
        template_id=template.template.id,
        run_id=image_result.run_id,
        with_logs=with_logs,
        model_override=override_video_model,
        duration_override=override_video_duration,
        intensity_override=override_video_intensity,
        steady_override=override_video_steady,
        overscan_pct=video_overscan_pct,
    )

    click.echo("\nVIDEO PASS DONE")
    click.echo(f"  output:      {video_result.output_path}")
    click.echo(f"  fal url:     {video_result.output_url}")
    click.echo(f"  wall time:   {video_result.duration_wall_sec}s")
    click.echo(
        f"  metadata →   {Path(image_result.output_dir) / 'metadata.json'}, "
        f"{Path(image_result.output_dir) / 'video_metadata.json'}"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
