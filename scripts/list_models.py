"""list_models — print the registered fal image-edit models with notes.

    python -m scripts.list_models
"""

from __future__ import annotations

import click

from i2v.models import list_known_models


@click.command(help=__doc__)
def main() -> None:
    models = list_known_models()
    width = max(len(m.id) for m in models)
    click.echo(f"\n{len(models)} known image models:\n")
    for m in models:
        defaults = ", ".join(m.default_for) if m.default_for else "—"
        click.secho(f"  {m.id:<{width}}  ", fg="cyan", nl=False)
        click.secho(m.label, bold=True)
        click.echo(f"    default for: {defaults}")
        # Wrap notes at ~80 chars
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
