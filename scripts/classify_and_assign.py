"""classify_and_assign — classify a directory of photos against a template
and produce an AssignmentPlan (which photo goes to which slot, end-frame
strategy per slot, etc.).

Houses are subfolders under inputs/. Each house is a separate photoshoot.

Usage:

    # Pick a house by name (recommended)
    python -m scripts.classify_and_assign \\
        --template templates/cinematic-editorial-v1.json \\
        --house thatcher

    # Or point at any folder explicitly
    python -m scripts.classify_and_assign \\
        --template templates/cinematic-editorial-v1.json \\
        --inputs inputs/thatcher

    # Or pass explicit photo paths
    python -m scripts.classify_and_assign \\
        --template templates/cinematic-editorial-v1.json \\
        --photos inputs/thatcher/IMG_5293.JPG,inputs/thatcher/IMG_5316.JPG

    # Discover available houses
    python -m scripts.classify_and_assign --list-houses
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click

from i2v.classifier import (
    ALTERNATE_ANGLE_THRESHOLD_DEFAULT,
    classify_and_assign,
)
from i2v.types import load_template


SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
INPUTS_ROOT = Path("inputs")
OUTPUTS_ROOT = Path("outputs")


def _slugify(name: str) -> str:
    """Filesystem-safe slug for a house name. '622 Camino Santa Barbara' → '622-camino-santa-barbara'."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "house"


def _list_houses() -> list[Path]:
    """Subdirectories of inputs/ that contain at least one supported photo."""
    if not INPUTS_ROOT.exists():
        return []
    out: list[Path] = []
    for p in sorted(INPUTS_ROOT.iterdir()):
        if not p.is_dir():
            continue
        has_photos = any(
            f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
            for f in p.iterdir()
        )
        if has_photos:
            out.append(p)
    return out


def _resolve_house(house: str) -> Path:
    """Resolve a house identifier to a directory under inputs/.

    Accepts an exact name, a slug, or a case-insensitive prefix match.
    """
    candidates = _list_houses()
    if not candidates:
        raise click.UsageError("No houses found under inputs/.")

    # Exact name match
    for c in candidates:
        if c.name == house:
            return c

    # Slug match
    target_slug = _slugify(house)
    for c in candidates:
        if _slugify(c.name) == target_slug:
            return c

    # Case-insensitive prefix match
    matches = [c for c in candidates if c.name.lower().startswith(house.lower())]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(c.name for c in matches)
        raise click.UsageError(
            f"Ambiguous house '{house}'. Matches: {names}. Be more specific."
        )

    available = ", ".join(c.name for c in candidates)
    raise click.UsageError(
        f"House '{house}' not found under inputs/. Available: {available}"
    )


def _gather_photos(inputs_dir: Path | None, photos_csv: str | None) -> list[Path]:
    if photos_csv:
        return [Path(p.strip()).resolve() for p in photos_csv.split(",") if p.strip()]
    if inputs_dir is None:
        return []
    return sorted(
        p.resolve()
        for p in inputs_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


@click.command(help=__doc__)
@click.option(
    "--template",
    "template_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the template JSON.",
)
@click.option(
    "--house",
    default=None,
    type=str,
    help="Name of a subfolder under inputs/ (e.g. 'thatcher'). Auto-namespaces "
    "outputs under outputs/<slug>/. Mutually exclusive with --inputs and --photos.",
)
@click.option(
    "--inputs",
    "inputs_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Directory of photos. Mutually exclusive with --house and --photos.",
)
@click.option(
    "--photos",
    "photos_csv",
    default=None,
    type=str,
    help="Comma-separated photo paths. Mutually exclusive with --house and --inputs.",
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False),
    help="Write the AssignmentPlan to this exact path. Default: "
    "outputs/<house-slug>/assignments/<timestamp>_plan.json",
)
@click.option(
    "--threshold",
    default=ALTERNATE_ANGLE_THRESHOLD_DEFAULT,
    show_default=True,
    type=float,
    help="Alternate-angle confidence threshold. ≥ this → real_reference end frame. "
    "Below → multi_ref_inpaint.",
)
@click.option(
    "--list-houses",
    "list_houses_flag",
    is_flag=True,
    default=False,
    help="Print available houses (subfolders under inputs/) and exit.",
)
@click.option(
    "--quiet/--verbose",
    default=False,
    help="Suppress per-photo classification output.",
)
def main(
    template_path: str | None,
    house: str | None,
    inputs_dir: str | None,
    photos_csv: str | None,
    output_path: str | None,
    threshold: float,
    list_houses_flag: bool,
    quiet: bool,
) -> None:
    if list_houses_flag:
        houses = _list_houses()
        if not houses:
            click.echo("No houses found under inputs/.")
            return
        click.echo("\nAvailable houses (subfolders under inputs/):\n")
        for h in houses:
            n_photos = sum(
                1 for f in h.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
            )
            click.echo(f"  {h.name:50s}  ({n_photos} photo{'s' if n_photos != 1 else ''})")
        click.echo()
        return

    if not template_path:
        raise click.UsageError("--template is required (unless using --list-houses).")

    sources = [bool(house), bool(inputs_dir), bool(photos_csv)]
    if sum(sources) != 1:
        raise click.UsageError(
            "Provide exactly one of --house <name>, --inputs <dir>, or --photos <csv>."
        )

    house_slug: str | None = None
    if house:
        house_dir = _resolve_house(house)
        house_slug = _slugify(house_dir.name)
        photos = _gather_photos(house_dir, None)
    elif inputs_dir:
        in_path = Path(inputs_dir)
        photos = _gather_photos(in_path, None)
        # If the dir is under inputs/, derive a slug from its name.
        if INPUTS_ROOT in in_path.resolve().parents or in_path.resolve().parent == INPUTS_ROOT.resolve():
            house_slug = _slugify(in_path.name)
    else:
        photos = _gather_photos(None, photos_csv)

    if not photos:
        click.secho("No photos found.", fg="red")
        sys.exit(2)

    template = load_template(template_path)

    click.echo(
        f"Template:  {template.template.id} ({len(template.slots)} slot(s))\n"
        f"House:     {house_slug or '(unspecified)'}\n"
        f"Photos:    {len(photos)} from {photos[0].parent}\n"
        f"Threshold: {threshold}\n"
    )

    plan = classify_and_assign(
        photo_paths=photos,
        template=template,
        alternate_angle_threshold=threshold,
        verbose=not quiet,
    )

    # Render summary to stdout
    click.echo("\n=== ASSIGNMENT PLAN ===\n")
    for assignment in plan.slot_assignments:
        if not assignment.is_active:
            click.secho(
                f"  ✗ {assignment.slot_id}: INACTIVE — {assignment.inactive_reason}",
                fg="yellow",
            )
            continue
        click.secho(f"  ✓ {assignment.slot_id}: ACTIVE", fg="green")
        primary = assignment.primary_classification
        click.echo(
            f"      primary:        {Path(assignment.primary_photo_path).name} "
            f"(hero={primary.hero_score:.2f} conf={primary.confidence:.2f})"
        )
        click.echo(f"      end strategy:   {assignment.end_frame_strategy}")
        if assignment.end_frame_photo_path:
            click.echo(
                f"      end frame:      {Path(assignment.end_frame_photo_path).name} "
                f"(score={assignment.end_frame_match_confidence:.2f})"
            )
        if assignment.additional_reference_photo_paths:
            ref_names = ", ".join(
                Path(p).name for p in assignment.additional_reference_photo_paths
            )
            click.echo(f"      refs:           {ref_names}")
        click.echo()

    if plan.unassigned_photo_paths:
        click.echo("\nUnassigned photos (no slot wanted them, not used as refs):")
        for p in plan.unassigned_photo_paths:
            cls = plan.photo_classifications.get(p)
            click.echo(
                f"  - {Path(p).name}  scene={cls.scene_kind if cls else '?'} "
                f"hero={cls.hero_score if cls else '?'}"
            )

    # Write JSON plan. When a house is identified, namespace the output under
    # outputs/<house-slug>/assignments/ so different houses don't comingle.
    if output_path is None:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        if house_slug:
            out = OUTPUTS_ROOT / house_slug / "assignments" / f"{ts}_plan.json"
        else:
            out = OUTPUTS_ROOT / "assignments" / f"{ts}_plan.json"
    else:
        out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan.model_dump_json(indent=2))
    click.echo(f"\nPlan written to: {out}")


if __name__ == "__main__":  # pragma: no cover
    main()
