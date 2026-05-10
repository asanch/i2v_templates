"""run_video — run a slot's video pass on a start-frame image.

Typical use after the image pipeline has produced a final frame:

    python -m scripts.run_video \\
        --template templates/cinematic-editorial-v1.json \\
        --slot 03_kitchen_wide_truck \\
        --image outputs/.../pass_01_editorial_enhance.png

It validates the template, looks up the slot, applies the slot's video_pass
spec to the given image, downloads the resulting mp4 into outputs/.

For ad-hoc prompt iteration without a template, use the lower-level CLI:

    python -m i2v.video_pass --input <png> --prompt "..." --duration 6
"""

from __future__ import annotations

from pathlib import Path

import click

from i2v.types import get_slot, load_template
from i2v.video_pass import run_video_pass


@click.command(help=__doc__)
@click.option(
    "--template",
    "template_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--slot",
    "slot_id",
    required=True,
    type=str,
    help="Id of the slot whose video_pass spec to use.",
)
@click.option(
    "--image",
    "input_image_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Local path to the start-frame image (typically a previous image-pipeline output).",
)
@click.option(
    "--end-frame",
    "end_frame_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Optional end keyframe for arrival/departure shots.",
)
@click.option(
    "--output-root",
    default="outputs",
    show_default=True,
    type=click.Path(file_okay=False),
)
@click.option(
    "--override-model",
    default=None,
    type=str,
    help="If set, ignore the template's video model and use this id instead.",
)
@click.option(
    "--override-duration",
    default=None,
    type=int,
    help="If set, override the template's duration_sec (clamped to model limits).",
)
@click.option(
    "--override-intensity",
    default=None,
    type=float,
    help="DepthFlow only: override the preset's intensity (DepthFlow range 0-4). "
    "Higher = camera moves further. Speed = intensity / duration; for 'further AND "
    "faster' raise intensity and keep duration short. Sign is preserved (the "
    "slow_dolly_out preset stays a pull-back).",
)
@click.option(
    "--override-steady",
    default=None,
    type=float,
    help="DepthFlow only: override the --steady value (range -1..2). Lower values "
    "(0.0 to -0.3) anchor the FOREGROUND so the focal subject stretches less; "
    "higher values anchor the background. Default in presets is ~0.3.",
)
@click.option(
    "--overscan",
    "overscan_pct",
    default=0.10,
    show_default=True,
    type=float,
    help="DepthFlow only: render at this fraction larger then ffmpeg-crop the "
    "stretched edges. 0.10 = render 10%% larger, crop back. Set to 0 to disable.",
)
@click.option("--with-logs/--no-logs", default=True)
def main(
    template_path: str,
    slot_id: str,
    input_image_path: str,
    end_frame_path: str | None,
    output_root: str,
    override_model: str | None,
    override_duration: int | None,
    override_intensity: float | None,
    override_steady: float | None,
    overscan_pct: float,
    with_logs: bool,
) -> None:
    template = load_template(template_path)
    slot = get_slot(template, slot_id)

    if slot.video_pass is None:
        raise click.UsageError(
            f"Slot '{slot_id}' has no video_pass declared in the template."
        )

    click.echo(
        f"Template: {template.template.id} ({template.template.name})\n"
        f"Slot:     {slot.id} — {slot.label}\n"
        f"Model:    {override_model or slot.video_pass.model}\n"
        f"Duration: {override_duration or slot.video_pass.duration_sec}s\n"
        f"Image:    {input_image_path}\n"
    )
    if end_frame_path:
        click.echo(f"End frame: {end_frame_path}")

    # One run dir per video render, mirroring the image-pipeline layout.
    from datetime import datetime, timezone
    import uuid

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_id = f"{ts}_{slot.id}_video_{uuid.uuid4().hex[:6]}"
    out_dir = Path(output_root) / run_id

    result = run_video_pass(
        input_image_path=input_image_path,
        video_pass_spec=slot.video_pass,
        output_dir=out_dir,
        slot_id=slot.id,
        template_id=template.template.id,
        run_id=run_id,
        end_frame_image_path=end_frame_path,
        with_logs=with_logs,
        model_override=override_model,
        duration_override=override_duration,
        intensity_override=override_intensity,
        steady_override=override_steady,
        overscan_pct=overscan_pct,
    )

    click.echo("\nDONE")
    click.echo(f"  run_id:      {result.run_id}")
    click.echo(f"  output:      {result.output_path}")
    click.echo(f"  fal url:     {result.output_url}")
    click.echo(f"  wall time:   {result.duration_wall_sec}s")
    click.echo(f"  metadata:    {Path(out_dir) / 'video_metadata.json'}")


if __name__ == "__main__":  # pragma: no cover
    main()
