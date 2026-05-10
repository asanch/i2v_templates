"""run_v2v — apply a video-to-video restyle pass to any mp4.

Pipeline pattern: produce a DepthFlow base clip (architecture-locked motion),
then layer a generative restyle for cinematic atmosphere on top.

Two ways to specify the style:

    # 1. Use a built-in conservative preset
    python -m scripts.run_v2v \\
        --input outputs/.../03_kitchen_wide_truck_clip.mp4 \\
        --style cinematic_warm

    # 2. Pass a custom prompt
    python -m scripts.run_v2v \\
        --input outputs/.../03_kitchen_wide_truck_clip.mp4 \\
        --prompt "Apply subtle cinematic film grain..."

List the built-in presets with:
    python -m scripts.run_v2v --list-styles
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from i2v.video_restyle import (
    PROMPT_PRESETS,
    list_prompt_presets,
    run_v2v_restyle,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


@click.command(help=__doc__)
@click.option(
    "--input",
    "input_video_path",
    type=click.Path(exists=True, dir_okay=False),
    required=False,
    help="Local mp4 to restyle (typically a DepthFlow output).",
)
@click.option(
    "--style",
    "style_preset",
    default=None,
    type=str,
    help=f"Built-in prompt preset. One of: {', '.join(PROMPT_PRESETS.keys())}",
)
@click.option(
    "--prompt",
    default=None,
    type=str,
    help="Custom restyle prompt. Mutually exclusive with --style.",
)
@click.option(
    "--enhance-prompt/--no-enhance-prompt",
    default=False,
    show_default=True,
    help="If True, fal expands the prompt before generation. Default off — we "
    "want our wording used verbatim for conservative restyle.",
)
@click.option(
    "--seed",
    default=None,
    type=int,
    help="Optional seed for reproducibility.",
)
@click.option(
    "--slot-id",
    default="adhoc",
    show_default=True,
    help="Stamped into output filename and metadata.",
)
@click.option(
    "--output-root",
    default="outputs",
    show_default=True,
    type=click.Path(file_okay=False),
)
@click.option(
    "--list-styles",
    is_flag=True,
    default=False,
    help="Print the built-in style presets and exit.",
)
@click.option("--with-logs/--no-logs", default=True)
def main(
    input_video_path: str | None,
    style_preset: str | None,
    prompt: str | None,
    enhance_prompt: bool,
    seed: int | None,
    slot_id: str,
    output_root: str,
    list_styles: bool,
    with_logs: bool,
) -> None:
    if list_styles:
        click.echo("\nAvailable style presets:\n")
        for name, full_prompt in list_prompt_presets():
            click.secho(f"  {name}", bold=True, fg="cyan")
            for line in full_prompt.split(". "):
                if line.strip():
                    click.echo(f"    {line.strip()}.")
            click.echo()
        return

    if not input_video_path:
        click.secho("--input is required (unless using --list-styles)", fg="red")
        sys.exit(2)

    # Resolve effective prompt.
    if style_preset and prompt:
        click.secho(
            "--style and --prompt are mutually exclusive. Pick one.", fg="red"
        )
        sys.exit(2)
    if style_preset:
        if style_preset not in PROMPT_PRESETS:
            click.secho(
                f"Unknown style preset {style_preset!r}. Available: "
                f"{', '.join(PROMPT_PRESETS.keys())}",
                fg="red",
            )
            sys.exit(2)
        effective_prompt = PROMPT_PRESETS[style_preset]
        prompt_label = style_preset
    elif prompt:
        effective_prompt = prompt
        prompt_label = "custom"
    else:
        click.secho(
            "Must provide either --style <preset> or --prompt <text>", fg="red"
        )
        sys.exit(2)

    out_dir = Path(output_root) / f"{_timestamp()}_{slot_id}_v2v_{prompt_label}"

    click.echo(
        f"Input:    {input_video_path}\n"
        f"Style:    {prompt_label}\n"
        f"Prompt:   {effective_prompt[:100]}{'...' if len(effective_prompt) > 100 else ''}\n"
        f"Seed:     {seed}\n"
        f"Output:   {out_dir}\n"
    )

    result = run_v2v_restyle(
        input_video_path=input_video_path,
        output_dir=out_dir,
        prompt=effective_prompt,
        enhance_prompt=enhance_prompt,
        seed=seed,
        slot_id=slot_id,
        with_logs=with_logs,
    )

    click.echo("\nDONE")
    click.echo(f"  output:    {result['output_path']}")
    click.echo(f"  fal url:   {result['output_url']}")
    click.echo(f"  wall time: {result['wall_time_sec']}s")
    click.echo(f"  metadata:  {Path(out_dir) / 'v2v_metadata.json'}")


if __name__ == "__main__":  # pragma: no cover
    main()
