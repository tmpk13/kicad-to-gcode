"""GRBL gcode emitter, matching the dialect of the known good EasyTrace output."""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import Cell, Layout, Polyline, path_length

M4_DYNAMIC = "M4"
M3_CONSTANT = "M3"


@dataclass(frozen=True)
class GcodeOptions:
    s_max: float = 1000.0
    laser_mode: str = M4_DYNAMIC
    decimals: int = 3
    rapid_rate: float = 3000.0
    return_home: bool = True
    preamble: tuple[str, ...] = ()
    postamble: tuple[str, ...] = ()


@dataclass
class Job:
    text: str
    cut_length: float = 0.0
    travel_length: float = 0.0
    cut_seconds: float = 0.0
    travel_seconds: float = 0.0
    path_count: int = 0
    records: list[dict] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return self.cut_seconds + self.travel_seconds


class _Writer:
    """Tracks modal state so S and F are only repeated when they change."""

    def __init__(self, options: GcodeOptions) -> None:
        self.options = options
        self.lines: list[str] = []
        self.position: tuple[float, float] | None = None
        self.power: float | None = None
        self.speed: float | None = None
        self.cut_length = 0.0
        self.travel_length = 0.0
        self.path_count = 0

    def comment(self, text: str) -> None:
        self.lines.append(f"; {text}" if text else ";")

    def blank(self) -> None:
        self.lines.append("")

    def raw(self, text: str) -> None:
        self.lines.append(text)

    def coord(self, value: float) -> str:
        text = f"{value:.{self.options.decimals}f}"
        # -0.000 reads badly on a machine display and confuses diffs.
        return "0" + text[6:] if text.startswith("-0.000") else text

    def path(self, points: Polyline, power: float, speed: float) -> None:
        if len(points) < 2:
            return
        start = points[0]
        if self.position is not None:
            self.travel_length += _distance(self.position, start)
        self.raw(f"G0 X{self.coord(start[0])} Y{self.coord(start[1])}")
        # Feed 1 mm/min keeps dynamic power near zero until the cut move sets F.
        self.raw("G1F1")
        self.speed = 1.0
        for index, point in enumerate(points[1:]):
            words = f"G1 X{self.coord(point[0])} Y{self.coord(point[1])}"
            if index == 0:
                words += f" S{power:.2f} F{_feed(speed)}"
                self.power = power
                self.speed = speed
            self.raw(words)
        self.raw("G1F1")
        self.raw(f"{self.options.laser_mode} S0")
        self.speed = 1.0
        self.power = 0.0
        self.cut_length += path_length(points)
        self.position = points[-1]
        self.path_count += 1


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _feed(speed: float) -> str:
    return f"{speed:.0f}" if abs(speed - round(speed)) < 1e-9 else f"{speed:.2f}"


def generate(layout: Layout, options: GcodeOptions) -> Job:
    """Emit the whole sweep and report how long it should take."""
    config = layout.config
    writer = _Writer(options)

    writer.raw("G21         ; Set units to mm")
    writer.raw("G90         ; Absolute positioning")
    writer.raw("G1F1")
    writer.raw(f"{options.laser_mode} S0")
    for line in options.preamble:
        writer.raw(line)
    writer.blank()
    for line in _header_comments(layout, options):
        writer.comment(line)

    records: list[dict] = []
    for cell in layout.travel_order():
        writer.blank()
        writer.comment(
            f"cell spot={cell.spot:g} power={cell.power:g} speed={cell.speed:g}"
            f" block={cell.block} row={cell.row} col={cell.col}"
        )
        before = writer.cut_length
        for path in cell.paths:
            writer.path(path, cell.power, cell.speed)
        records.append(_record(cell, writer.cut_length - before))

    if layout.label_paths:
        writer.blank()
        writer.comment(
            f"labels power={layout.label_power:g} speed={layout.label_speed:g}"
        )
        for path in layout.label_paths:
            writer.path(path, layout.label_power, layout.label_speed)

    writer.blank()
    for line in options.postamble:
        writer.raw(line)
    writer.raw("M5          ; Switch tool off")
    if options.return_home:
        writer.raw(f"G0 X{writer.coord(config.origin[0])} Y{writer.coord(config.origin[1])}")

    cut_seconds = sum(record["seconds"] for record in records)
    cut_seconds += _label_seconds(layout)
    travel_seconds = (
        writer.travel_length / options.rapid_rate * 60.0 if options.rapid_rate else 0.0
    )
    return Job(
        text="\n".join(writer.lines) + "\n",
        cut_length=writer.cut_length,
        travel_length=writer.travel_length,
        cut_seconds=cut_seconds,
        travel_seconds=travel_seconds,
        path_count=writer.path_count,
        records=records,
    )


def _record(cell: Cell, length: float) -> dict:
    return {
        "block": cell.block,
        "row": cell.row,
        "col": cell.col,
        "spot": cell.spot,
        "power": cell.power,
        "speed": cell.speed,
        "x": cell.center[0],
        "y": cell.center[1],
        "passes": len(cell.paths),
        "length": length,
        "seconds": length / cell.speed * 60.0 if cell.speed else 0.0,
    }


def _label_seconds(layout: Layout) -> float:
    if not layout.label_paths or not layout.label_speed:
        return 0.0
    total = sum(path_length(path) for path in layout.label_paths)
    return total / layout.label_speed * 60.0


def _header_comments(layout: Layout, options: GcodeOptions) -> list[str]:
    config = layout.config
    min_x, min_y, max_x, max_y = layout.burn_bounds
    return [
        "Laser parameter sweep",
        f"Power (S):    {_values(config.powers)} of {options.s_max:g}",
        f"Speed (F):    {_values(config.speeds)} mm/min",
        f"Spot size:    {_values(config.spots)} mm",
        f"Line:         {config.line_length:g} x {config.line_width:g} mm,"
        f" fill {config.fill}, overlap {config.overlap:g}, passes {config.passes}",
        f"Grid:         {len(config.speeds)} speed x {len(config.powers)} power"
        f" x {len(config.spots)} spot = {len(layout.cells)} cells",
        f"Extent:       X {min_x:.2f} to {max_x:.2f}, Y {min_y:.2f} to {max_y:.2f} mm",
        "Labels:       left P=power, bottom F=speed, top d=spot",
        "",
    ]


def _values(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values)
