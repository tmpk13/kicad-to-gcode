"""Markdown map of the sweep: where each cell is and what it was burned with."""

from __future__ import annotations

from .gcode import Job
from .layout import Layout


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
        f"| Line | {config.line_length:g} x {config.line_width:g} mm |",
        f"| Fill | {config.fill}, overlap {config.overlap:g},"
        f" {config.passes} pass(es) |",
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

    lines.extend(_cell_maps(layout))
    lines.extend(_worksheet(job))
    lines.extend(_timeline(layout, job))
    return "\n".join(lines) + "\n"


def _cell_maps(layout: Layout) -> list[str]:
    config = layout.config
    lines = ["## Cell centers (machine mm)", ""]
    for block, spot in enumerate(config.spots):
        lines.append(f"### Spot {spot:g} mm")
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


def _worksheet(job: Job) -> list[str]:
    lines = [
        "## Worksheet",
        "",
        "Measure each line and fill in the last two columns.",
        "",
        "| Spot | Power | Speed | X | Y | Time | Measured width | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in job.records:
        lines.append(
            f"| {record['spot']:g} | {record['power']:g} | {record['speed']:g}"
            f" | {record['x']:.2f} | {record['y']:.2f}"
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


def _values(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m {rest:02d}s"
