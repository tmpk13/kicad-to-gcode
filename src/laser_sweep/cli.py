"""Command line entry point for the laser parameter sweep generator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, gcode, layout, preview, report
from .params import RangeError, check_positive, check_power, parse_range

RANGE_HELP = (
    "value, comma list, min:max:count[:log], or min:max:+step"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laser-sweep",
        description="Generate a GRBL gcode parameter sweep for a UV laser: "
        "spot size against speed against power, burned as lines of a set width.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=f"Every range takes: {RANGE_HELP}",
    )
    parser.add_argument("--version", action="version", version=__version__)

    ranges = parser.add_argument_group("sweep ranges")
    ranges.add_argument("--power", default="200:800:4", help="laser power, S units")
    ranges.add_argument("--speed", default="300:900:4", help="feed rate, mm/min")
    ranges.add_argument(
        "--spot", default="0.05,0.1,0.15", help="beam spot size, mm"
    )

    line = parser.add_argument_group("line geometry")
    line.add_argument("--line-length", type=float, default=5.0, help="mm")
    width = line.add_mutually_exclusive_group()
    width.add_argument("--line-width", type=float, default=0.5, help="mm")
    width.add_argument(
        "--line-spots",
        type=float,
        help="line width as a multiple of each block's own spot size, so every"
        " block burns the same number of passes instead of the same mm",
    )
    line.add_argument(
        "--overlap",
        type=float,
        default=0.0,
        help="fraction of the spot that adjacent passes share, 0 to 0.95",
    )
    line.add_argument("--passes", type=int, default=1, help="repeats of each line")
    line.add_argument(
        "--fill",
        choices=(layout.SERPENTINE, layout.UNIDIRECTIONAL),
        default=layout.SERPENTINE,
        help="serpentine keeps the beam on through the turns, solid lines only",
    )
    line.add_argument(
        "--invert",
        action="store_true",
        help="clear a border around an uncut island instead of burning a solid"
        " line; --line-width is then the width left standing",
    )
    border = line.add_mutually_exclusive_group()
    border.add_argument(
        "--border",
        type=float,
        default=0.4,
        help="mm cleared around the island, --invert only",
    )
    border.add_argument(
        "--border-spots",
        type=float,
        help="border as a multiple of each block's own spot size, --invert only",
    )
    line.add_argument(
        "--frame-order",
        choices=(layout.INSIDE_OUT, layout.OUTSIDE_IN),
        default=layout.INSIDE_OUT,
        help="which frame loop runs first, --invert only",
    )

    place = parser.add_argument_group("placement")
    place.add_argument("--gap-x", type=float, default=3.0, help="mm between columns")
    place.add_argument("--gap-y", type=float, default=2.0, help="mm between rows")
    place.add_argument("--block-gap", type=float, default=6.0, help="mm between blocks")
    place.add_argument(
        "--block-cols", type=int, default=1, help="spot blocks per row of blocks"
    )
    place.add_argument("--origin-x", type=float, default=0.0)
    place.add_argument("--origin-y", type=float, default=0.0)
    place.add_argument(
        "--anchor",
        choices=("center", "corner"),
        default="center",
        help="what the origin refers to: the middle or the lower left corner",
    )
    place.add_argument(
        "--fit",
        metavar="WxH",
        help="fail unless the sweep fits in this area, mm",
    )
    place.add_argument(
        "--force", action="store_true", help="write the file even if --fit fails"
    )

    labels = parser.add_argument_group("burned labels")
    labels.add_argument("--no-labels", dest="labels", action="store_false")
    labels.add_argument("--label-height", type=float, default=1.6, help="mm")
    labels.add_argument("--label-gutter", type=float, default=1.0, help="mm")
    labels.add_argument(
        "--no-label-prefix",
        dest="label_prefix",
        action="store_false",
        help="drop the P, F and d letters in front of label numbers",
    )
    labels.add_argument(
        "--label-power", type=float, help="default: middle of the power range"
    )
    labels.add_argument(
        "--label-speed", type=float, help="default: middle of the speed range"
    )

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
    machine.add_argument(
        "--no-return-home", dest="return_home", action="store_false"
    )
    machine.add_argument(
        "--approach",
        type=float,
        default=0.0,
        help="mm: rapid to a point this far below and left of every path start,"
        " then rapid into it, so the first cut moves never reverse an axis and"
        " backlash stays out of the burned feature; 0 disables",
    )
    machine.add_argument(
        "--preamble", action="append", default=[], help="extra line after the header"
    )
    machine.add_argument(
        "--postamble", action="append", default=[], help="extra line before M5"
    )

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--output", default="out/sweep.gcode", type=Path)
    output.add_argument("--map", dest="map_path", type=Path, help="markdown map")
    output.add_argument("--preview", dest="preview_path", type=Path, help="SVG preview")
    output.add_argument("--no-map", dest="want_map", action="store_false")
    output.add_argument("--no-preview", dest="want_preview", action="store_false")
    output.add_argument(
        "-n", "--dry-run", action="store_true", help="report but write nothing"
    )
    return parser


def _parse_fit(text: str) -> tuple[float, float]:
    parts = text.lower().replace(",", "x").split("x")
    if len(parts) != 2:
        raise ValueError(f"--fit expects WxH, got {text!r}")
    return float(parts[0]), float(parts[1])


def _config(args: argparse.Namespace) -> layout.SweepConfig:
    powers = check_power(parse_range(args.power, "power"), args.s_max)
    speeds = check_positive(parse_range(args.speed, "speed"), "speed")
    spots = check_positive(parse_range(args.spot, "spot"), "spot")
    if args.line_length <= 0 or args.line_width <= 0:
        raise ValueError("line length and width must be > 0")
    if args.passes < 1:
        raise ValueError("passes must be >= 1")
    if args.approach < 0:
        raise ValueError("approach must be >= 0")
    if not 0.0 <= args.overlap < 1.0:
        raise ValueError("overlap must be in [0, 1)")
    if args.line_spots is not None and args.line_spots <= 0:
        raise ValueError("line-spots must be > 0")
    if args.invert:
        if args.border_spots is not None:
            if args.border_spots < 1.0:
                raise ValueError(
                    f"border-spots {args.border_spots:g} is under 1, so the frame"
                    " would be narrower than the beam cutting it; use 1 or more"
                )
        elif args.border <= 0:
            raise ValueError("border must be > 0")
        elif args.border < max(spots):
            raise ValueError(
                f"border {args.border:g} mm is narrower than the widest spot"
                f" {max(spots):g} mm, so the frame cannot be cut without eating"
                " into the island; raise --border, lower --spot, or size it in"
                " spots with --border-spots"
            )
    return layout.SweepConfig(
        powers=powers,
        speeds=speeds,
        spots=spots,
        line_length=args.line_length,
        line_width=args.line_width,
        overlap=args.overlap,
        passes=args.passes,
        fill=args.fill,
        line_spots=args.line_spots,
        invert=args.invert,
        border=args.border,
        border_spots=args.border_spots,
        frame_order=args.frame_order,
        gap_x=args.gap_x,
        gap_y=args.gap_y,
        block_gap=args.block_gap,
        block_cols=args.block_cols,
        origin=(args.origin_x, args.origin_y),
        anchor=args.anchor,
        labels=args.labels,
        label_height=args.label_height,
        label_gutter=args.label_gutter,
        label_prefix=args.label_prefix,
        label_power=args.label_power,
        label_speed=args.label_speed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _config(args)
    except (RangeError, ValueError) as exc:
        parser.error(str(exc))

    plan = layout.build(config)
    job = gcode.generate(
        plan,
        gcode.GcodeOptions(
            s_max=args.s_max,
            laser_mode=args.laser_mode,
            decimals=args.decimals,
            rapid_rate=args.rapid_rate,
            return_home=args.return_home,
            approach=args.approach,
            preamble=tuple(args.preamble),
            postamble=tuple(args.postamble),
        ),
    )

    min_x, min_y, max_x, max_y = plan.burn_bounds
    size = (max_x - min_x, max_y - min_y)
    summary = [
        f"cells      {len(plan.cells)}"
        f" ({len(config.speeds)} speed x {len(config.powers)} power"
        f" x {len(config.spots)} spot)",
        f"paths      {job.path_count}",
        f"size       {size[0]:.2f} x {size[1]:.2f} mm",
        f"extent     X {min_x:.2f} to {max_x:.2f}, Y {min_y:.2f} to {max_y:.2f} mm",
        f"cut        {job.cut_length:.1f} mm, travel {job.travel_length:.1f} mm",
        f"estimate   {job.seconds / 60.0:.1f} min",
    ]

    if args.fit:
        try:
            fit_w, fit_h = _parse_fit(args.fit)
        except ValueError as exc:
            parser.error(str(exc))
        if size[0] > fit_w + 1e-9 or size[1] > fit_h + 1e-9:
            message = (
                f"sweep is {size[0]:.2f} x {size[1]:.2f} mm,"
                f" which does not fit {fit_w:g} x {fit_h:g} mm"
            )
            if not args.force:
                print("\n".join(summary), file=sys.stderr)
                parser.error(message + " (use --force to write it anyway)")
            print(f"warning: {message}", file=sys.stderr)

    written: list[Path] = []
    if not args.dry_run:
        out = args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(job.text, encoding="ascii")
        written.append(out)

        if args.want_map:
            map_path = args.map_path or out.with_suffix(out.suffix + ".map.md")
            map_path.parent.mkdir(parents=True, exist_ok=True)
            map_path.write_text(report.render(plan, job, out.name), encoding="ascii")
            written.append(map_path)

        if args.want_preview:
            preview_path = (
                args.preview_path or out.with_suffix(out.suffix + ".preview.svg")
            )
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_text(preview.render(plan), encoding="ascii")
            written.append(preview_path)

    print("\n".join(summary))
    for path in written:
        print(f"wrote      {path}")
    if args.dry_run:
        print("dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
