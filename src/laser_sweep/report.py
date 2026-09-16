"""Markdown map of the sweep: where each cell is and what it was burned with."""

from __future__ import annotations

from .gcode import Job
from .layout import Layout, border_for, line_width_for


def render(layout: Layout, job: Job, gcode_name: str) -> str:
    config = layout.config
    min_x, min_y, max_x, max_y = layout.burn_bounds
    lines: list[str] = [
        "# Laser parameter sweep map",
        "",
        f"Generated for `{gcode_name}`.",
        "",
        "## Parameters",
        "",
        "| Setting | Value |",
        "| --- | --- |",
        f"| Power (S) | {_values(config.powers)} |",
        f"| Speed (F) | {_values(config.speeds)} mm/min |",
        f"| Spot size | {_values(config.spots)} mm |",
        f"| {_shape_name(config)} | {config.line_length:g} mm x"
        f" {_sized(config, config.line_spots, config.line_width, line_width_for)} |",
        f"| {_fill_name(config)} | {_fill_value(config)},"
        f" overlap {config.overlap:g}, {config.passes} pass(es) |",
    ]
    lines.extend(_machine_rows(job))
    lines += [
        f"| Cells | {len(layout.cells)} |",
        f"| Paths | {job.path_count} |",
        f"| Cut length | {job.cut_length:.1f} mm |",
        f"| Travel length | {job.travel_length:.1f} mm |",
        f"| Estimated time | {_clock(job.seconds)} |",
        f"| Extent (burned) | X {min_x:.2f} to {max_x:.2f},"
        f" Y {min_y:.2f} to {max_y:.2f} mm |",
        "",
        "## Reading the coupon",
        "",
        "Each block is one spot size, labelled `d` above its top left corner.",
        "Within a block, rows are power (`P`, left edge, increasing upwards) and",
        "columns are speed (`F`, bottom edge, increasing to the right).",
        "",
    ]
    if config.invert:
        border = _sized(config, config.border_spots, config.border, border_for)
        island = _sized(config, config.line_spots, config.line_width, line_width_for)
        lines.extend(
            [
                f"Each cell clears a {border} border around an island meant to be"
                f" left standing at {island} wide.",
                "Measure the island, not the cut: a beam wider than the machine is"
                " set up for eats into it from both sides.",
                "",
            ]
        )

    lines.extend(_cell_maps(layout))
    lines.extend(_worksheet(job, config.invert))
    lines.extend(_timeline(layout, job))
    return "\n".join(lines) + "\n"


def _cell_maps(layout: Layout) -> list[str]:
    config = layout.config
    lines = ["## Cell centers (machine mm)", ""]
    for block, spot in enumerate(config.spots):
        lines.append(f"### Spot {spot:g} mm{_block_sizes(config, spot)}")
        lines.append("")
        header = "| Power \\ Speed | " + " | ".join(
            f"{speed:g}" for speed in config.speeds
        )
        lines.append(header + " |")
        lines.append("| --- " * (len(config.speeds) + 1) + "|")
        for row in reversed(range(len(config.powers))):
            cells = [
                cell
                for cell in layout.cells
                if cell.block == block and cell.row == row
            ]
            cells.sort(key=lambda cell: cell.col)
            body = " | ".join(
                f"{cell.center[0]:.2f}, {cell.center[1]:.2f}" for cell in cells
            )
            lines.append(f"| {config.powers[row]:g} | {body} |")
        lines.append("")
    return lines


def _worksheet(job: Job, invert: bool) -> list[str]:
    feature = "island" if invert else "line"
    lines = [
        "## Worksheet",
        "",
        f"Measure each {feature} and fill in the last two columns.",
        "",
        f"| Spot | Power | Speed | Target {feature} | X | Y | Time"
        f" | Measured {feature} | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in job.records:
        lines.append(
            f"| {record['spot']:g} | {record['power']:g} | {record['speed']:g}"
            f" | {record['width']:g} mm | {record['x']:.2f} | {record['y']:.2f}"
            f" | {record['seconds']:.1f} s |  |  |"
        )
    lines.append("")
    return lines


def _timeline(layout: Layout, job: Job) -> list[str]:
    lines = [
        "## Estimated timeline",
        "",
        "```mermaid",
        "gantt",
        "    title Burn time by spot size block",
        "    dateFormat X",
        "    axisFormat %M:%S",
        "    section Blocks",
    ]
    start = 0
    for block, spot in enumerate(layout.config.spots):
        seconds = sum(
            record["seconds"] for record in job.records if record["block"] == block
        )
        # Mermaid needs a non-zero duration for the bar to render.
        duration = max(1, int(round(seconds)))
        lines.append(f"    spot {spot:g} mm :b{block}, {start}, {duration}s")
        start += duration
    overhead = int(round(job.seconds)) - start
    if overhead > 0:
        lines.append(f"    labels and travel :bx, {start}, {overhead}s")
    lines.append("```")
    lines.append("")
    return lines


def _machine_rows(job: Job) -> list[str]:
    options = job.options
    rows: list[str] = []
    if options is None:
        return rows
    if options.approach > 0:
        rows.append(f"| Approach | {options.approach:g} mm below left of every path start |")
    if any(options.backlash):
        bx, by = options.backlash
        rows.append(f"| Backlash | X {bx:g} mm, Y {by:g} mm |")
    return rows


def _shape_name(config) -> str:
    return "Island" if config.invert else "Line"


def _fill_name(config) -> str:
    return "Frame" if config.invert else "Fill"


def _fill_value(config) -> str:
    if config.invert:
        border = _sized(config, config.border_spots, config.border, border_for)
        return f"{border} border, loops {config.frame_order}"
    return config.fill


def _sized(config, count, fixed: float, resolver) -> str:
    """A fixed millimetre size, or a spot multiple with what it works out as."""
    if count is None:
        return f"{fixed:g} mm"
    values = ", ".join(f"{resolver(config, spot):g}" for spot in config.spots)
    return f"{count:g} spots ({values} mm)"


def _block_sizes(config, spot: float) -> str:
    """Resolved sizes for one block, shown only when they vary with the spot."""
    parts = []
    if config.line_spots is not None:
        name = "island" if config.invert else "line"
        parts.append(f"{name} {line_width_for(config, spot):g} mm")
    if config.invert and config.border_spots is not None:
        parts.append(f"border {border_for(config, spot):g} mm")
    return f" ({', '.join(parts)})" if parts else ""


def _values(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m {rest:02d}s"
