"""Geometry of the sweep: cell fills, block placement and burned labels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import font

Point = tuple[float, float]
Polyline = list[Point]

SERPENTINE = "serpentine"
UNIDIRECTIONAL = "unidirectional"


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


def build(config: SweepConfig) -> Layout:
    """Place every cell and label, then move the result onto the origin."""
    if not config.powers or not config.speeds or not config.spots:
        raise ValueError("powers, speeds and spots must all be non-empty")

    rows = len(config.powers)
    cols = len(config.speeds)
    pitch_x = config.line_length + config.gap_x
    pitch_y = config.line_width + config.gap_y
    grid_w = cols * config.line_length + (cols - 1) * config.gap_x
    grid_h = rows * config.line_width + (rows - 1) * config.gap_y

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
            cy = grid_y0 + config.line_width / 2.0 + row * pitch_y
            for col, speed in enumerate(config.speeds):
                cx = grid_x0 + config.line_length / 2.0 + col * pitch_x
                cell = Cell(
                    block=block_index,
                    row=row,
                    col=col,
                    spot=spot,
                    power=power,
                    speed=speed,
                    center=(cx, cy),
                )
                cell.paths = fill_line(
                    cell.center,
                    config.line_length,
                    config.line_width,
                    spot,
                    config.overlap,
                    config.fill,
                    config.passes,
                )
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
                cx = grid_x0 + config.line_length / 2.0 + col * pitch_x
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
