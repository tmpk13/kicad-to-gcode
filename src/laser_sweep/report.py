"""Markdown map of the sweep: where each cell is and what it was burned with."""

from __future__ import annotations

from .backlash import format_coord
from .gcode import Job
from .layout import Layout, blocks, border_for, line_width_for


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
    lines.extend(_machine_rows(job, config))
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
        "Each block is one spot size"
        + (
            " and one assumed backlash, labelled `d` and `b`"
            if config.backlashes
            else ", labelled `d`"
        )
        + " above its top left corner.",
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
    for block, (spot, lash) in enumerate(blocks(config)):
        heading = f"### Spot {spot:g} mm{_block_sizes(config, spot)}"
        if lash is not None:
            heading += f", backlash {lash:g} mm"
        lines.append(heading)
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
    swept = any(record.get("backlash") is not None for record in job.records)
    lash_head = " Backlash |" if swept else ""
    lines = [
        "## Worksheet",
        "",
        f"Measure each {feature} and fill in the last two columns.",
        "",
        f"| Spot |{lash_head} Power | Speed | Target {feature} | X | Y | Time"
        f" | Measured {feature} | Notes |",
        "| --- " * (9 + int(swept)) + "|",
    ]
    for record in job.records:
        lash = f" {record['backlash']:g} |" if swept else ""
        lines.append(
            f"| {record['spot']:g} |{lash} {record['power']:g}"
            f" | {record['speed']:g}"
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
        "    title Burn time by block",
        "    dateFormat X",
        "    axisFormat %M:%S",
        "    section Blocks",
    ]
    start = 0
    for block, (spot, lash) in enumerate(blocks(layout.config)):
        seconds = sum(
            record["seconds"] for record in job.records if record["block"] == block
        )
        # Mermaid needs a non-zero duration for the bar to render.
        duration = max(1, int(round(seconds)))
        name = f"spot {spot:g} mm"
        if lash is not None:
            name += f" backlash {lash:g} mm"
        lines.append(f"    {name} :b{block}, {start}, {duration}s")
        start += duration
    overhead = int(round(job.seconds)) - start
    if overhead > 0:
        lines.append(f"    labels and travel :bx, {start}, {overhead}s")
    lines.append("```")
    lines.append("")
    return lines


def _machine_rows(job: Job, config=None) -> list[str]:
    options = job.options
    rows: list[str] = []
    if options is None:
        return rows
    if options.approach > 0:
        rows.append(f"| Approach | {options.approach:g} mm below left of every path start |")
    if config is not None and config.backlashes:
        rows.append(
            f"| Backlash | swept {_values(config.backlashes)} mm,"
            " one block each, both axes |"
        )
    elif any(options.backlash):
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


def render_iso(plan, job: Job, gcode_name: str, source: str) -> str:
    """Markdown map of an isolation job: what it cuts and what it cannot."""
    config = plan.config
    min_x, min_y, max_x, max_y = plan.bounds
    offsets = ", ".join(f"{value:.3f}" for value in plan.offsets)
    lines: list[str] = [
        "# Isolation job map",
        "",
        f"Generated for `{gcode_name}` from `{source}`.",
        "",
        "## Parameters",
        "",
        "| Setting | Value |",
        "| --- | --- |",
        f"| Spot size | {config.spot:g} mm |",
        f"| Loops | {len(plan.offsets)} at {offsets} mm from the copper edge |",
        f"| Cleared band | {_band(plan):g} mm each side of every feature |",
        f"| Overlap | {config.overlap:g} |",
        f"| Loop order | {config.order} |",
        f"| Mirror | {config.mirror} |",
        f"| Placement | {config.anchor} at"
        f" {config.origin[0]:g}, {config.origin[1]:g} mm |",
    ]
    lines.extend(_machine_rows(job))
    lines += [
        f"| Copper islands | {plan.islands} |",
        f"| Paths | {job.path_count} |",
        f"| Cut length | {job.cut_length:.1f} mm |",
        f"| Travel length | {job.travel_length:.1f} mm |",
        f"| Estimated time | {_clock(job.seconds)} |",
        f"| Extent (burned) | X {format_coord(min_x, 2)} to {format_coord(max_x, 2)},"
        f" Y {format_coord(min_y, 2)} to {format_coord(max_y, 2)} mm |",
        "",
    ]

    lines += ["## Phases", "", "| Phase | Paths | S | F | Cut mm | Time |", "| --- | --- | --- | --- | --- | --- |"]
    for record in job.records:
        lines.append(
            f"| {record['label']} | {record['paths']} | {record['power']:g}"
            f" | {record['speed']:g} | {record['length']:.1f}"
            f" | {_clock(record['seconds'])} |"
        )
    lines.append("")

    if plan.bridges:
        lines += [
            "## Copper the beam cannot part",
            "",
            f"{plan.bridged_total} group(s) of copper sit closer together than the"
            f" {plan.offsets[0]:.3f} mm the first loop needs, so the beam grows"
            " through the gap and they stay connected. Cut the spot size, or"
            " open the clearance in the layout.",
            "",
            "| Islands | X mm | Y mm | Clearance mm | Spot that would fit |",
            "| --- | --- | --- | --- | --- |",
        ]
        for bridge in plan.bridges:
            fits = max(0.0, bridge.clearance - 2.0 * config.margin)
            lines.append(
                f"| {bridge.islands} | {bridge.at[0]:.2f} | {bridge.at[1]:.2f}"
                f" | {bridge.clearance:.3f} | under {fits:.3f} |"
            )
        if plan.bridged_total > len(plan.bridges):
            lines.append("")
            lines.append(
                f"{plan.bridged_total - len(plan.bridges)} further group(s) not listed."
            )
        lines.append("")

    lines.extend(_iso_timeline(job))
    return "\n".join(lines) + "\n"


def _band(plan) -> float:
    """How wide the cleared band around a feature comes out."""
    return plan.offsets[-1] + plan.config.spot / 2.0


def _iso_timeline(job: Job) -> list[str]:
    lines = [
        "## Estimated timeline",
        "",
        "```mermaid",
        "gantt",
        "    title Burn time by phase",
        "    dateFormat X",
        "    axisFormat %M:%S",
        "    section Phases",
    ]
    start = 0
    for index, record in enumerate(job.records):
        duration = max(1, int(round(record["seconds"])))
        lines.append(f"    {record['label']} :p{index}, {start}, {duration}s")
        start += duration
    overhead = int(round(job.seconds)) - start
    if overhead > 0:
        lines.append(f"    travel :px, {start}, {overhead}s")
    lines.append("```")
    lines.append("")
    return lines
