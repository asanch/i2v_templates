"""list_video_models — print the registered fal image-to-video models with notes.

    python -m scripts.list_video_models
"""

from __future__ import annotations

import click

from i2v.video_models import list_known_video_models


@click.command(help=__doc__)
def main() -> None:
    models = list_known_video_models()
    width = max(len(m.id) for m in models)
    click.echo(f"\n{len(models)} known video models:\n")
    for m in models:
        durations = (
            f"{m.allowed_durations}"
            if m.allowed_durations
            else f"{m.min_duration}-{m.max_duration}s"
        )
        end_frame = "yes" if m.supports_end_frame else "no"
        click.secho(f"  {m.id:<{width}}  ", fg="cyan", nl=False)
        click.secho(m.label, bold=True)
        click.echo(f"    durations: {durations}    end-frame: {end_frame}    cost: ${m.cost_per_sec}/sec")
        notes = m.notes
        while notes:
            chunk = notes[:80]
            split = chunk.rfind(" ") if len(notes) > 80 else len(chunk)
            split = split if split > 0 else len(chunk)
            click.echo(f"    {notes[:split].strip()}")
            notes = notes[split:].strip()
        click.echo()


if __name__ == "__main__":  # pragma: no cover
    main()
