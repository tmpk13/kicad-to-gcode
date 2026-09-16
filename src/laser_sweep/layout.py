"""Geometry of the sweep: cell fills, block placement and burned labels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import font

Point = tuple[float, float]
Polyline = list[Point]

SERPENTINE = "serpentine"
UNIDIRECTIONAL = "unidirectional"

INSIDE_OUT = "inside-out"
OUTSIDE_IN = "outside-in"


@dataclass(frozen=True)
class SweepConfig:
    powers: list[float]
    speeds: list[float]
    spots: list[float]
    line_length: float = 10.0
    line_width: float = 1.0
    overlap: float = 0.0
    passes: int = 1
    fill: str = SERPENTINE
    line_spots: float | None = None
    invert: bool = False
    border: float = 0.4
    border_spots: float | None = None
    frame_order: str = INSIDE_OUT
    gap_x: float = 3.0
    gap_y: float = 2.0
    block_gap: float = 6.0
    block_cols: int = 1
    origin: Point = (0.0, 0.0)
    anchor: str = "center"
    labels: bool = True
    label_height: float = 1.6
    label_gutter: float = 1.0
    label_prefix: bool = True
    label_power: float | None = None
    label_speed: float | None = None


@dataclass
class Cell:
    block: int
    row: int
    col: int
    spot: float
    power: float
    speed: float
    center: Point
    width: float = 0.0
    border: float = 0.0
    paths: list[Polyline] = field(default_factory=list)

    @property
    def cut_length(self) -> float:
        return sum(path_length(path) for path in self.paths)


@dataclass
class Layout:
    config: SweepConfig
    cells: list[Cell]
    label_paths: list[Polyline]
    label_power: float
    label_speed: float
    bounds: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @property
    def burn_bounds(self) -> tuple[float, float, float, float]:
        """Path bounds grown by the beam radius, i.e. the material actually hit."""
        margin = max(self.config.spots) / 2.0
        min_x, min_y, max_x, max_y = self.bounds
        return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)

    def travel_order(self) -> list["Cell"]:
        """Cells in cut order: boustrophedon within each block to cut travel."""
        ordered: list[Cell] = []
        blocks = sorted({cell.block for cell in self.cells})
        for block in blocks:
            in_block = [cell for cell in self.cells if cell.block == block]
            rows = sorted({cell.row for cell in in_block})
            for index, row in enumerate(rows):
                in_row = sorted(
                    (cell for cell in in_block if cell.row == row),
                    key=lambda cell: cell.col,
                    reverse=index % 2 == 1,
                )
                ordered.extend(in_row)
        return ordered


def path_length(path: Polyline) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def pass_offsets(width: float, spot: float, overlap: float) -> list[float]:
    """Offsets from the line center for each parallel pass of the fill.

    A line of `width` is produced by beams of diameter `spot`, so the pass
    centers only have to span `width - spot`; the beam radius covers the rest.
    """
    if spot <= 0:
        raise ValueError("spot must be > 0")
    if not 0.0 <= overlap < 1.0:
        raise ValueError("overlap must be in [0, 1)")
    span = width - spot
    step = spot * (1.0 - overlap)
    if span <= 1e-9:
        return [0.0]
    count = int(math.ceil(span / step - 1e-9)) + 1
    return [-span / 2.0 + span * i / (count - 1) for i in range(count)]


def fill_line(
    center: Point,
    length: float,
    width: float,
    spot: float,
    overlap: float,
    fill: str,
    passes: int = 1,
) -> list[Polyline]:
    """Polylines that burn one horizontal line of the requested width."""
    cx, cy = center
    x0 = cx - length / 2.0
    x1 = cx + length / 2.0
    ys = [cy + off for off in pass_offsets(width, spot, overlap)]

    single: list[Polyline]
    if fill == SERPENTINE:
        points: Polyline = []
        for index, y in enumerate(ys):
            left_to_right = index % 2 == 0
            points.append((x0 if left_to_right else x1, y))
            points.append((x1 if left_to_right else x0, y))
        single = [points]
    elif fill == UNIDIRECTIONAL:
        single = [[(x0, y), (x1, y)] for y in ys]
    else:
        raise ValueError(f"unknown fill mode {fill!r}")

    return [list(path) for _ in range(max(1, passes)) for path in single]


def frame_offsets(border: float, spot: float, overlap: float) -> list[float]:
    """Distances from the island edge to each loop of the surrounding frame.

    The innermost loop sits half a spot outside the island so its beam edge
    stops exactly on the island boundary, which is the width being measured.
    A border thinner than the spot cannot hold a pass without eating into the
    island, so it is widened to one spot rather than narrowing the island.
    """
    if spot <= 0:
        raise ValueError("spot must be > 0")
    if border <= 0:
        raise ValueError("border must be > 0")
    if border - spot <= 1e-9:
        return [spot / 2.0]
    return [border / 2.0 + offset for offset in pass_offsets(border, spot, overlap)]


def frame_loops(
    center: Point,
    length: float,
    width: float,
    spot: float,
    overlap: float,
    border: float,
    passes: int = 1,
    order: str = INSIDE_OUT,
) -> list[Polyline]:
    """Closed loops that clear a border around an uncut island of `width`."""
    if order not in (INSIDE_OUT, OUTSIDE_IN):
        raise ValueError(f"unknown frame order {order!r}")
    cx, cy = center
    half_x = length / 2.0
    half_y = width / 2.0
    offsets = frame_offsets(border, spot, overlap)
    if order == OUTSIDE_IN:
        offsets = list(reversed(offsets))

    single: list[Polyline] = []
    for distance in offsets:
        x0 = cx - half_x - distance
        x1 = cx + half_x + distance
        y0 = cy - half_y - distance
        y1 = cy + half_y + distance
        single.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])

    return [list(path) for _ in range(max(1, passes)) for path in single]


def line_width_for(config: SweepConfig, spot: float) -> float:
    """Line width for one block, in mm or as a multiple of that block's spot."""
    if config.line_spots is not None:
        return config.line_spots * spot
    return config.line_width


def border_for(config: SweepConfig, spot: float) -> float:
    """Cleared border for one block, in mm or as a multiple of its spot."""
    if config.border_spots is not None:
        return config.border_spots * spot
    return config.border


def cell_size(config: SweepConfig, spot: float) -> tuple[float, float]:
    """Footprint of one cell: the island plus its frame when inverted."""
    width = line_width_for(config, spot)
    if config.invert:
        border = border_for(config, spot)
        return (
            config.line_length + 2.0 * border,
            width + 2.0 * border,
        )
    return (config.line_length, width)


def slot_size(config: SweepConfig) -> tuple[float, float]:
    """Grid pitch, sized to the largest cell so every block stays aligned."""
    sizes = [cell_size(config, spot) for spot in config.spots]
    return (max(size[0] for size in sizes), max(size[1] for size in sizes))


def cell_paths(config: SweepConfig, center: Point, spot: float) -> list[Polyline]:
    width = line_width_for(config, spot)
    if config.invert:
        return frame_loops(
            center,
            config.line_length,
            width,
            spot,
            config.overlap,
            border_for(config, spot),
            config.passes,
            config.frame_order,
        )
    return fill_line(
        center,
        config.line_length,
        width,
        spot,
        config.overlap,
        config.fill,
        config.passes,
    )


def build(config: SweepConfig) -> Layout:
    """Place every cell and label, then move the result onto the origin."""
    if not config.powers or not config.speeds or not config.spots:
        raise ValueError("powers, speeds and spots must all be non-empty")

    rows = len(config.powers)
    cols = len(config.speeds)
    cell_w, cell_h = slot_size(config)
    pitch_x = cell_w + config.gap_x
    pitch_y = cell_h + config.gap_y
    grid_w = cols * cell_w + (cols - 1) * config.gap_x
    grid_h = rows * cell_h + (rows - 1) * config.gap_y

    label_power = config.label_power
    if label_power is None:
        label_power = _median(config.powers)
    label_speed = config.label_speed
    if label_speed is None:
        label_speed = _median(config.speeds)

    if config.labels:
        row_texts = [_label("P", value, config) for value in config.powers]
        left_gutter = (
            max(font.text_width(text, config.label_height) for text in row_texts)
            + config.label_gutter
        )
        bottom_gutter = config.label_height + config.label_gutter
        top_header = config.label_height + config.label_gutter
    else:
        row_texts = []
        left_gutter = bottom_gutter = top_header = 0.0

    block_w = left_gutter + grid_w
    block_h = bottom_gutter + grid_h + top_header
    block_pitch_x = block_w + config.block_gap
    block_pitch_y = block_h + config.block_gap
    block_cols = max(1, config.block_cols)

    cells: list[Cell] = []
    label_paths: list[Polyline] = []

    for block_index, spot in enumerate(config.spots):
        bc = block_index % block_cols
        br = block_index // block_cols
        # Blocks read left to right, then downwards, so block 0 is top left.
        grid_x0 = bc * block_pitch_x + left_gutter
        grid_y0 = -br * block_pitch_y - top_header - grid_h

        for row, power in enumerate(config.powers):
            cy = grid_y0 + cell_h / 2.0 + row * pitch_y
            for col, speed in enumerate(config.speeds):
                cx = grid_x0 + cell_w / 2.0 + col * pitch_x
                cell = Cell(
                    block=block_index,
                    row=row,
                    col=col,
                    spot=spot,
                    power=power,
                    speed=speed,
                    center=(cx, cy),
                    width=line_width_for(config, spot),
                    border=border_for(config, spot),
                )
                cell.paths = cell_paths(config, cell.center, spot)
                cells.append(cell)

            if config.labels:
                label_paths.extend(
                    font.text_paths(
                        row_texts[row],
                        grid_x0 - config.label_gutter,
                        cy,
                        config.label_height,
                        anchor="rc",
                    )
                )

        if config.labels:
            for col, speed in enumerate(config.speeds):
                cx = grid_x0 + cell_w / 2.0 + col * pitch_x
                label_paths.extend(
                    font.text_paths(
                        _label("F", speed, config),
                        cx,
                        grid_y0 - config.label_gutter,
                        config.label_height,
                        anchor="ct",
                    )
                )
            label_paths.extend(
                font.text_paths(
                    _label("d", spot, config),
                    grid_x0,
                    grid_y0 + grid_h + config.label_gutter,
                    config.label_height,
                    anchor="lb",
                )
            )

    bounds = _bounds(cells, label_paths)
    offset = _placement_offset(bounds, config)
    if offset != (0.0, 0.0):
        dx, dy = offset
        for cell in cells:
            cell.center = (cell.center[0] + dx, cell.center[1] + dy)
            cell.paths = [[(x + dx, y + dy) for x, y in path] for path in cell.paths]
        label_paths = [[(x + dx, y + dy) for x, y in path] for path in label_paths]
        bounds = (
            bounds[0] + dx,
            bounds[1] + dy,
            bounds[2] + dx,
            bounds[3] + dy,
        )

    return Layout(
        config=config,
        cells=cells,
        label_paths=label_paths,
        label_power=label_power,
        label_speed=label_speed,
        bounds=bounds,
    )


def _label(prefix: str, value: float, config: SweepConfig) -> str:
    text = font.format_number(value)
    return f"{prefix}{text}" if config.label_prefix else text


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _bounds(
    cells: list[Cell], label_paths: list[Polyline]
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for cell in cells:
        for path in cell.paths:
            for x, y in path:
                xs.append(x)
                ys.append(y)
    for path in label_paths:
        for x, y in path:
            xs.append(x)
            ys.append(y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _placement_offset(
    bounds: tuple[float, float, float, float], config: SweepConfig
) -> Point:
    min_x, min_y, max_x, max_y = bounds
    ox, oy = config.origin
    if config.anchor == "center":
        return (ox - (min_x + max_x) / 2.0, oy - (min_y + max_y) / 2.0)
    if config.anchor == "corner":
        return (ox - min_x, oy - min_y)
    raise ValueError(f"unknown anchor {config.anchor!r}")
