"""run_starlight — upscale a video via Topaz Starlight (diffusion-based detail
reconstruction) on fal.

Pipeline order: produce a DepthFlow base clip (architecture-locked motion +
overscan crop), then run it through Starlight to clean up remaining edge
stretching and optionally upscale to 4K.

Examples:

    # Default: subtle 1.5x upscale, max original-detail preservation
    python -m scripts.run_starlight \\
        --input outputs/<run>/06_kitchen_detail_85mm_clip.mp4 \\
        --preset starlight_subtle

    # 4K upscale with the newest Starlight Precise 2.5
    python -m scripts.run_starlight \\
        --input outputs/<run>/06_kitchen_detail_85mm_clip.mp4 \\
        --preset starlight_4k

    # Cinematic film grain look
    python -m scripts.run_starlight \\
        --input outputs/<run>/06_kitchen_detail_85mm_clip.mp4 \\
        --preset starlight_film

List the built-in presets with:
    python -m scripts.run_starlight --list-presets
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from i2v.video_upscale import (
    KNOWN_TOPAZ_MODELS,
    PRESETS,
    list_presets,
    run_starlight_upscale,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


@click.command(help=__doc__)
@click.option(
    "--input",
    "input_video_path",
    type=click.Path(exists=True, dir_okay=False),
    required=False,
    help="Local mp4 to upscale (typically a DepthFlow output).",
)
@click.option(
    "--preset",
    default="starlight_subtle",
    show_default=True,
    type=click.Choice(list(PRESETS.keys()) + ["none"]),
    help=f"Preset configuration. 'none' = specify everything via individual flags.",
)
@click.option(
    "--model",
    default=None,
    type=str,
    help="Override the preset's model (e.g. 'Starlight Precise 2.5').",
)
@click.option(
    "--upscale-factor",
    default=None,
    type=float,
    help="Override upscale factor. Range 1-4. (1080p × 2 = 4K)",
)
@click.option(
    "--recover-detail",
    default=None,
    type=float,
    help="0..1. Higher = preserve more original detail. Default 0.85 in our presets "
    "to bias against Starlight's tendency to invent.",
)
@click.option(
    "--grain",
    default=None,
    type=float,
    help="0..1. Adds film grain. Use for cinematic/editorial look.",
)
@click.option(
    "--noise",
    default=None,
    type=float,
    help="0..1. Denoising strength. Our depthflow source isn't noisy; usually 0.",
)
@click.option(
    "--target-fps",
    default=None,
    type=int,
    help="If set (16-60), interpolates frames to this FPS.",
)
@click.option(
    "--slot-id",
    default="adhoc",
    show_default=True,
)
@click.option(
    "--output-root",
    default="outputs",
    show_default=True,
    type=click.Path(file_okay=False),
)
@click.option(
    "--list-presets",
    "list_presets_flag",
    is_flag=True,
    default=False,
    help="Print the built-in presets and exit.",
)
@click.option(
    "--list-models",
    is_flag=True,
    default=False,
    help="Print all available Topaz models and exit.",
)
@click.option("--with-logs/--no-logs", default=True)
def main(
    input_video_path: str | None,
    preset: str,
    model: str | None,
    upscale_factor: float | None,
    recover_detail: float | None,
    grain: float | None,
    noise: float | None,
    target_fps: int | None,
    slot_id: str,
    output_root: str,
    list_presets_flag: bool,
    list_models: bool,
    with_logs: bool,
) -> None:
    if list_models:
        click.echo("\nAvailable Topaz models on fal:\n")
        click.echo("Classical (no generative repair):")
        for m in KNOWN_TOPAZ_MODELS:
            if not m.startswith("Starlight"):
                click.echo(f"  - {m}")
        click.echo("\nStarlight (diffusion-based generative restoration):")
        for m in KNOWN_TOPAZ_MODELS:
            if m.startswith("Starlight"):
                click.echo(f"  - {m}")
        return

    if list_presets_flag:
        click.echo("\nAvailable presets:\n")
        for name, notes in list_presets():
            click.secho(f"  {name}", bold=True, fg="cyan")
            for line in notes.split(". "):
                if line.strip():
                    click.echo(f"    {line.strip()}.")
            click.echo()
        return

    if not input_video_path:
        click.secho(
            "--input is required (unless using --list-presets / --list-models)",
            fg="red",
        )
        sys.exit(2)

    effective_preset: str | None = preset if preset != "none" else None

    out_dir = Path(output_root) / f"{_timestamp()}_{slot_id}_starlight_{preset}"

    click.echo(
        f"Input:    {input_video_path}\n"
        f"Preset:   {preset}\n"
        f"Output:   {out_dir}\n"
    )

    result = run_starlight_upscale(
        input_video_path=input_video_path,
        output_dir=out_dir,
        preset=effective_preset,
        model=model,
        upscale_factor=upscale_factor,
        recover_detail=recover_detail,
        grain=grain,
        noise=noise,
        target_fps=target_fps,
        slot_id=slot_id,
        with_logs=with_logs,
    )

    click.echo("\nDONE")
    click.echo(f"  output:    {result['output_path']}")
    click.echo(f"  fal url:   {result['output_url']}")
    click.echo(f"  wall time: {result['wall_time_sec']}s")
    click.echo(f"  metadata:  {Path(out_dir) / 'starlight_metadata.json'}")


if __name__ == "__main__":  # pragma: no cover
    main()
