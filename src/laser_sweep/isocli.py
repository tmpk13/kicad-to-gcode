"""Command line entry point for isolation gcode straight from a board or gerber."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, excellon, gcode, geom, gerber, isolate, preview, report
from .backlash import parse_backlash
from .isolate import (
    ANCHOR_CENTER,
    ANCHOR_CORNER,
    ANCHOR_KEEP,
    MIRROR_NONE,
    MIRROR_X,
    MIRROR_Y,
    IsoConfig,
)
from .layout import INSIDE_OUT, OUTSIDE_IN

KICAD_CLI = "kicad-cli"
AUTO, NONE = "auto", "none"


class SourceError(ValueError):
    """The board or gerber files could not be found or exported."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laser-iso",
        description="Turn a copper layer into isolation gcode for a UV laser:"
        " gerber or KiCad board in, GRBL out, with no other tool in between.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "input", type=Path, help="a .kicad_pcb, or a copper layer gerber"
    )
    parser.add_argument(
        "--layer",
        default="F.Cu",
        help="copper layer to isolate, for a board file or to pick a sibling gerber",
    )

    cut = parser.add_argument_group("isolation")
    cut.add_argument("--spot", type=float, default=0.1, help="beam diameter, mm")
    width = cut.add_mutually_exclusive_group()
    width.add_argument(
        "--passes", type=int, default=1, help="isolation loops around every feature"
    )
    width.add_argument(
        "--clear",
        type=float,
        help="mm of copper to clear around every feature, loops worked out from it",
    )
    cut.add_argument(
        "--overlap",
        type=float,
        default=0.0,
        help="fraction of the spot that adjacent loops share, 0 to 0.95",
    )
    cut.add_argument(
        "--margin",
        type=float,
        default=0.0,
        help="extra mm between the copper edge and the first loop",
    )
    cut.add_argument(
        "--order",
        choices=(INSIDE_OUT, OUTSIDE_IN),
        default=INSIDE_OUT,
        help="inside-out cuts the loop against the copper first, on cold material",
    )
    cut.add_argument(
        "--no-bridge-check",
        dest="bridge_check",
        action="store_false",
        help="skip the search for copper the beam cannot part",
    )

    place = parser.add_argument_group("placement")
    place.add_argument(
        "--mirror",
        choices=(MIRROR_NONE, MIRROR_X, MIRROR_Y),
        default=MIRROR_NONE,
        help="flip the layer, for cutting a bottom layer through the board",
    )
    place.add_argument(
        "--anchor",
        choices=(ANCHOR_CORNER, ANCHOR_CENTER, ANCHOR_KEEP),
        default=ANCHOR_CORNER,
        help="corner puts the lower left of the job on the origin, keep leaves the"
        " gerber coordinates alone",
    )
    place.add_argument("--origin-x", type=float, default=0.0)
    place.add_argument("--origin-y", type=float, default=0.0)
    place.add_argument("--fit", metavar="WxH", help="fail unless the job fits, mm")
    place.add_argument(
        "--force", action="store_true", help="write the file even if --fit fails"
    )

    burn = parser.add_argument_group("burn settings")
    burn.add_argument("--power", type=float, default=500.0, help="S for the isolation")
    burn.add_argument(
        "--speed", type=float, default=400.0, help="F for the isolation, mm/min"
    )

    edge = parser.add_argument_group("board outline")
    edge.add_argument(
        "--outline",
        default=NONE,
        help=f"{AUTO} to take the profile from the board or a sibling Edge_Cuts"
        f" gerber, {NONE}, or a path",
    )
    edge.add_argument(
        "--outline-offset",
        type=float,
        default=0.0,
        help="mm outside the profile to run the cut, 0 follows the line itself",
    )
    edge.add_argument("--outline-passes", type=int, default=1)
    edge.add_argument("--outline-power", type=float, help="default: --power")
    edge.add_argument("--outline-speed", type=float, help="default: --speed")

    holes = parser.add_argument_group("drill marks")
    holes.add_argument(
        "--drill",
        default=NONE,
        help=f"{AUTO} to take holes from the board or a sibling drill file,"
        f" {NONE}, or a path",
    )
    holes.add_argument(
        "--drill-marks",
        choices=(excellon.CROSS, excellon.CIRCLE, excellon.DOT),
        default=excellon.CROSS,
        help="what to burn at each hole center for a drill to follow",
    )
    holes.add_argument("--drill-power", type=float, help="default: --power")
    holes.add_argument("--drill-speed", type=float, help="default: --speed")

    machine = parser.add_argument_group("machine")
    machine.add_argument("--s-max", type=float, default=1000.0, help="max S value")
    machine.add_argument(
        "--laser-mode",
        choices=(gcode.M4_DYNAMIC, gcode.M3_CONSTANT),
        default=gcode.M4_DYNAMIC,
        help="M4 scales power with speed, M3 holds it constant",
    )
    machine.add_argument("--decimals", type=int, default=3, help="coordinate decimals")
    machine.add_argument(
        "--rapid-rate", type=float, default=3000.0, help="G0 speed for the estimate"
    )
    machine.add_argument("--no-return-home", dest="return_home", action="store_false")
    machine.add_argument(
        "--approach",
        type=float,
        default=0.0,
        help="mm: rapid to a point this far below and left of every path start,"
        " then rapid into it, so no cut starts on a direction reversal; 0 disables",
    )
    machine.add_argument(
        "--backlash",
        default="0",
        metavar="X[,Y]",
        help="mm of lost motion on a reversal, one value or X,Y",
    )
    machine.add_argument("--preamble", action="append", default=[])
    machine.add_argument("--postamble", action="append", default=[])

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--output", type=Path, help="default: out/<board>.gcode")
    output.add_argument("--map", dest="map_path", type=Path, help="markdown map")
    output.add_argument("--preview", dest="preview_path", type=Path, help="SVG preview")
    output.add_argument("--no-map", dest="want_map", action="store_false")
    output.add_argument("--no-preview", dest="want_preview", action="store_false")
    output.add_argument(
        "--gerber-dir",
        type=Path,
        help="where to leave gerbers exported from a board file, default: a"
        " temporary directory",
    )
    output.add_argument(
        "-n", "--dry-run", action="store_true", help="report but write nothing"
    )
    return parser


# --- finding the files -----------------------------------------------------


def layer_suffix(layer: str) -> str:
    return layer.replace(".", "_")


def export_board(board: Path, layer: str, outline: bool, drill: bool, into: Path) -> None:
    """Run kicad-cli so a board file needs no manual gerber export."""
    if shutil.which(KICAD_CLI) is None:
        raise SourceError(
            f"{KICAD_CLI} is not on PATH, so a .kicad_pcb cannot be read here;"
            " export the gerbers from KiCad and pass one of those instead"
        )
    into.mkdir(parents=True, exist_ok=True)
    layers = [layer] + (["Edge.Cuts"] if outline else [])
    _run(
        [
            KICAD_CLI, "pcb", "export", "gerbers",
            "--output", str(into),
            "--layers", ",".join(layers),
            "--no-netlist",
            str(board),
        ]
    )
    if drill:
        _run(
            [
                KICAD_CLI, "pcb", "export", "drill",
                "--output", str(into),
                "--excellon-units", "mm",
                str(board),
            ]
        )


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {result.returncode}"
        raise SourceError(f"{command[0]} failed: {message}")


def find_layer(directory: Path, layer: str) -> Path:
    suffix = layer_suffix(layer)
    found = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and suffix in path.name
        and path.suffix.lower() in (".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gm1")
    ]
    if not found:
        raise SourceError(f"no {layer} gerber in {directory}")
    return found[0]


def find_drill(directory: Path, stem: str) -> Path | None:
    holes = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".drl" and "NPTH" not in path.name
    )
    named = [path for path in holes if path.name.startswith(stem)]
    return (named or holes or [None])[0]


def outline_paths(image: gerber.Image) -> list[list[tuple[float, float]]]:
    """The profile as drawn: stroke centerlines and any filled contours."""
    paths: list[list[tuple[float, float]]] = []
    for figure in image.figures:
        if isinstance(figure, gerber.Draw):
            paths.append([tuple(point) for point in figure.points])
        else:
            for _, contour in figure.parts:
                closed = [tuple(point) for point in contour]
                paths.append(closed + [closed[0]])
    return paths


def outline_cut(paths: list, distance: float) -> list:
    """The profile itself, or a loop that many mm outside it."""
    if distance == 0.0:
        return [list(path) for path in paths]
    closed = [path[:-1] for path in paths if len(path) > 3 and path[0] == path[-1]]
    open_paths = [path for path in paths if not (len(path) > 3 and path[0] == path[-1])]
    grown = geom.offset(geom.union(closed), distance) if closed else []
    return [isolate.as_loop(contour) for contour in grown] + [
        list(path) for path in open_paths
    ]


def _parse_fit(text: str) -> tuple[float, float]:
    parts = text.lower().replace(",", "x").split("x")
    if len(parts) != 2:
        raise ValueError(f"--fit expects WxH, got {text!r}")
    return float(parts[0]), float(parts[1])


def _choice(value: str) -> str:
    return value.strip().lower()


def resolve(args, temporary: list) -> tuple[Path, Path | None, Path | None]:
    """Work out which files hold the copper, the profile and the holes."""
    source = args.input
    if not source.exists():
        raise SourceError(f"{source} does not exist")

    outline_mode = _choice(args.outline)
    drill_mode = _choice(args.drill)
    outline = None if outline_mode in (AUTO, NONE) else Path(args.outline)
    drill = None if drill_mode in (AUTO, NONE) else Path(args.drill)

    if source.suffix.lower() == ".kicad_pcb":
        directory = args.gerber_dir
        if directory is None:
            holder = tempfile.TemporaryDirectory(prefix="laser-iso-")
            temporary.append(holder)
            directory = Path(holder.name)
        export_board(
            source,
            args.layer,
            outline=outline_mode == AUTO,
            drill=drill_mode == AUTO,
            into=directory,
        )
        copper = find_layer(directory, args.layer)
        if outline_mode == AUTO:
            outline = find_layer(directory, "Edge.Cuts")
        if drill_mode == AUTO:
            drill = find_drill(directory, source.stem)
            if drill is None:
                raise SourceError(f"no drill file was exported to {directory}")
        return copper, outline, drill

    copper = source
    directory = source.parent
    if outline_mode == AUTO:
        outline = find_layer(directory, "Edge.Cuts")
    if drill_mode == AUTO:
        drill = find_drill(directory, source.stem.split("-")[0])
        if drill is None:
            raise SourceError(f"no drill file next to {copper}")
    return copper, outline, drill


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    temporary: list = []

    try:
        copper_path, outline_path, drill_path = resolve(args, temporary)
        backlash = parse_backlash(args.backlash)
        config = IsoConfig(
            spot=args.spot,
            passes=args.passes,
            clear=args.clear,
            overlap=args.overlap,
            margin=args.margin,
            order=args.order,
            mirror=args.mirror,
            anchor=args.anchor,
            origin=(args.origin_x, args.origin_y),
        )
        isolate.offsets_for(config)  # fail on bad numbers before any work
        if args.approach < 0:
            raise ValueError("approach must be >= 0")
        if args.power > args.s_max:
            raise ValueError(f"power {args.power:g} is over --s-max {args.s_max:g}")
        if args.speed <= 0:
            raise ValueError("speed must be > 0")

        image = gerber.read(copper_path)
        copper = geom.copper(image)
        if not copper:
            raise SourceError(f"{copper_path} draws no copper")

        outline = []
        if outline_path is not None:
            outline = outline_cut(
                outline_paths(gerber.read(outline_path)), args.outline_offset
            )
        drills = []
        holes = 0
        if drill_path is not None:
            found = excellon.read(drill_path)
            holes = len(found.holes)
            drills = excellon.marks(found, args.drill_marks, args.spot)
    except (SourceError, gerber.GerberError, ValueError) as exc:
        parser.error(str(exc))

    plan = isolate.build(
        copper,
        config,
        outline=outline,
        drills=drills,
        drill_holes=holes,
        check_bridges=args.bridge_check,
    )

    groups: list[gcode.PathGroup] = []
    for index in sorted({loop.index for loop in plan.loops}):
        members = [loop for loop in plan.loops if loop.index == index]
        groups.append(
            gcode.PathGroup(
                label=f"isolation loop {index + 1} at {members[0].offset:.3f} mm",
                paths=[loop.points for loop in members],
                power=args.power,
                speed=args.speed,
            )
        )
    if plan.outline:
        for repeat in range(max(1, args.outline_passes)):
            groups.append(
                gcode.PathGroup(
                    label=f"board outline pass {repeat + 1}",
                    paths=[list(path) for path in plan.outline],
                    power=args.outline_power or args.power,
                    speed=args.outline_speed or args.speed,
                )
            )
    if plan.drills:
        groups.append(
            gcode.PathGroup(
                label=f"drill marks ({holes} holes)",
                paths=[list(path) for path in plan.drills],
                power=args.drill_power or args.power,
                speed=args.drill_speed or args.speed,
            )
        )

    options = gcode.GcodeOptions(
        s_max=args.s_max,
        origin=(args.origin_x, args.origin_y),
        laser_mode=args.laser_mode,
        decimals=args.decimals,
        rapid_rate=args.rapid_rate,
        return_home=args.return_home,
        approach=args.approach,
        backlash=backlash,
        preamble=tuple(args.preamble),
        postamble=tuple(args.postamble),
    )
    job = gcode.generate_paths(groups, options, _header(plan, args, copper_path, holes))

    min_x, min_y, max_x, max_y = plan.bounds
    size = (max_x - min_x, max_y - min_y)
    summary = [
        f"source     {args.input}"
        + (f" ({copper_path.name})" if copper_path != args.input else ""),
        f"copper     {plan.islands} islands",
        f"loops      {len(plan.loops)} in {len(plan.offsets)} pass(es)"
        f" at {', '.join(f'{value:.3f}' for value in plan.offsets)} mm",
        f"paths      {job.path_count}",
        f"size       {size[0]:.2f} x {size[1]:.2f} mm",
        f"extent     X {min_x:.2f} to {max_x:.2f}, Y {min_y:.2f} to {max_y:.2f} mm",
        f"cut        {job.cut_length:.1f} mm, travel {job.travel_length:.1f} mm",
        f"burn       S{args.power:g} F{args.speed:g}",
        f"estimate   {job.seconds / 60.0:.1f} min",
    ]

    warnings: list[str] = []
    if plan.bridged_total:
        worst = plan.bridges[0] if plan.bridges else None
        detail = f", closest {worst.clearance:.3f} mm" if worst else ""
        warnings.append(
            f"{plan.bridged_total} group(s) of copper sit closer than the"
            f" {plan.offsets[0]:.3f} mm the first loop needs{detail}; they stay"
            " connected. Lower --spot or open the layout. See the map."
        )
    if "Bot" in image.file_function and args.mirror == MIRROR_NONE:
        warnings.append(
            f"{copper_path.name} is a bottom layer and --mirror is none, so the"
            " job comes out reversed unless the board is cut from below"
        )

    if args.fit:
        try:
            fit_w, fit_h = _parse_fit(args.fit)
        except ValueError as exc:
            parser.error(str(exc))
        if size[0] > fit_w + 1e-9 or size[1] > fit_h + 1e-9:
            message = (
                f"job is {size[0]:.2f} x {size[1]:.2f} mm,"
                f" which does not fit {fit_w:g} x {fit_h:g} mm"
            )
            if not args.force:
                print("\n".join(summary), file=sys.stderr)
                parser.error(message + " (use --force to write it anyway)")
            warnings.append(message)

    written: list[Path] = []
    if not args.dry_run:
        out = args.output or Path("out") / f"{copper_path.stem}.gcode"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(job.text, encoding="ascii")
        written.append(out)

        if args.want_map:
            map_path = args.map_path or out.with_suffix(out.suffix + ".map.md")
            map_path.parent.mkdir(parents=True, exist_ok=True)
            map_path.write_text(
                report.render_iso(plan, job, out.name, copper_path.name),
                encoding="ascii",
            )
            written.append(map_path)

        if args.want_preview:
            preview_path = args.preview_path or out.with_suffix(
                out.suffix + ".preview.svg"
            )
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text(preview.render_iso(plan), encoding="ascii")
            written.append(preview_path)

    print("\n".join(summary))
    for path in written:
        print(f"wrote      {path}")
    if args.dry_run:
        print("dry run, nothing written")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for holder in temporary:
        holder.cleanup()
    return 0


def _header(plan, args, source: Path, holes: int) -> list[str]:
    config = plan.config
    min_x, min_y, max_x, max_y = plan.bounds
    offsets = ", ".join(f"{value:.3f}" for value in plan.offsets)
    band = plan.offsets[-1] + config.spot / 2.0
    lines = [
        f"Isolation for {source.name}",
        f"Copper:       {plan.islands} islands",
        f"Spot size:    {config.spot:g} mm",
        f"Loops:        {len(plan.offsets)} at {offsets} mm from the copper edge,"
        f" clearing {band:g} mm",
        f"Order:        {config.order}, overlap {config.overlap:g}",
        f"Placement:    {config.anchor} at {config.origin[0]:g}, {config.origin[1]:g}"
        f" mm, mirror {config.mirror}",
        f"Extent:       X {min_x:.2f} to {max_x:.2f}, Y {min_y:.2f} to {max_y:.2f} mm",
        f"Isolation:    S{args.power:g} F{args.speed:g} of {args.s_max:g}",
    ]
    if plan.outline:
        lines.append(
            f"Outline:      {len(plan.outline)} path(s),"
            f" {args.outline_passes} pass(es), offset {args.outline_offset:g} mm,"
            f" S{args.outline_power or args.power:g}"
            f" F{args.outline_speed or args.speed:g}"
        )
    if plan.drills:
        lines.append(
            f"Drill marks:  {holes} holes as {args.drill_marks},"
            f" S{args.drill_power or args.power:g}"
            f" F{args.drill_speed or args.speed:g}"
        )
    if plan.bridged_total:
        lines.append(
            f"WARNING:      {plan.bridged_total} group(s) of copper are closer"
            " together than the beam can part; they stay connected"
        )
    if args.approach > 0:
        lines.append(
            f"Approach:     {args.approach:g} mm below left of every path start,"
            " rapid in with both axes positive"
        )
    if any(parse_backlash(args.backlash)):
        bx, by = parse_backlash(args.backlash)
        lines.append(
            f"Backlash:     X {bx:g} mm, Y {by:g} mm, crossed with the beam off"
            " at every reversal"
        )
    lines.append("")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
