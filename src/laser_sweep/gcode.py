"""GRBL gcode emitter, matching the dialect of the known good EasyTrace output."""

from __future__ import annotations

from dataclasses import dataclass, field

from .backlash import Compensator, format_coord
from .layout import (
    Cell,
    Layout,
    Polyline,
    border_for,
    line_width_for,
    path_length,
)

M4_DYNAMIC = "M4"
M3_CONSTANT = "M3"


@dataclass(frozen=True)
class GcodeOptions:
    s_max: float = 1000.0
    origin: tuple[float, float] = (0.0, 0.0)
    laser_mode: str = M4_DYNAMIC
    decimals: int = 3
    rapid_rate: float = 3000.0
    return_home: bool = True
    approach: float = 0.0
    backlash: tuple[float, float] = (0.0, 0.0)
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
    options: GcodeOptions | None = None

    @property
    def seconds(self) -> float:
        return self.cut_seconds + self.travel_seconds


class _Writer:
    """Tracks modal state so S and F are only repeated when they change."""

    def __init__(self, options: GcodeOptions) -> None:
        self.options = options
        self.lines: list[str] = []
        self.motion = Compensator(options.backlash)
        self.power: float | None = None
        self.speed: float | None = None
        self.cut_length = 0.0
        self.travel_length = 0.0
        self.path_count = 0

    @property
    def position(self) -> tuple[float, float] | None:
        return self.motion.position

    def comment(self, text: str) -> None:
        self.lines.append(f"; {text}" if text else ";")

    def blank(self) -> None:
        self.lines.append("")

    def raw(self, text: str) -> None:
        self.lines.append(text)

    def coord(self, value: float) -> str:
        return format_coord(value, self.options.decimals)

    def rapid(self, target: tuple[float, float]) -> None:
        """Rapid to `target`, entering it from below left when an approach is set.

        Both axes then arrive moving in the positive direction, so a path whose
        first moves are +X and +Y never reverses an axis and backlash stays out
        of the feature being burned.
        """
        approach = self.options.approach
        if approach > 0:
            self._rapid((target[0] - approach, target[1] - approach))
        self._rapid(target)

    def _rapid(self, target: tuple[float, float]) -> None:
        if self.position is not None:
            self.travel_length += _distance(self.position, target)
        x, y = self.motion.move(target)
        self.raw(f"G0 X{self.coord(x)} Y{self.coord(y)}")

    def retune(self, backlash: tuple[float, float]) -> None:
        """Compensate for a different lost motion from here on.

        The commanded position only differs from the head position while an
        axis is in the negative state, so the value is swapped with both axes
        positive and nothing has to be unwound. A positive rapid longer than
        either dead band puts them there.
        """
        if tuple(backlash) == tuple(self.motion.backlash):
            return
        reach = 2.0 * max(max(backlash), max(self.motion.backlash))
        if self.position is not None and any(
            direction < 0 for direction in self.motion.direction
        ):
            self.comment(f"both axes into the positive state before {backlash[0]:g} mm")
            self._rapid((self.position[0] + reach, self.position[1] + reach))
        self.motion.backlash = (backlash[0], backlash[1])

    def _take_up(self, target: tuple[float, float]) -> None:
        """Cross the dead band with the beam off before a cut reverses an axis."""
        take = self.motion.take_up(target)
        if take is not None:
            self.raw(f"G0 X{self.coord(take[0])} Y{self.coord(take[1])}")

    def path(self, points: Polyline, power: float, speed: float) -> None:
        if len(points) < 2:
            return
        self.rapid(points[0])
        self._take_up(points[1])
        self._open()
        first = True
        for point in points[1:]:
            if not first and self.motion.active and self.motion.reversals(point):
                self._close()
                self._take_up(point)
                self._open()
                first = True
            self._cut(point, power, speed, first)
            first = False
        self._close()

    def _open(self) -> None:
        # Feed 1 mm/min keeps dynamic power near zero until the cut move sets F.
        self.raw("G1F1")
        self.speed = 1.0

    def _cut(
        self, point: tuple[float, float], power: float, speed: float, first: bool
    ) -> None:
        start = self.position
        x, y = self.motion.move(point)
        words = f"G1 X{self.coord(x)} Y{self.coord(y)}"
        if first:
            words += f" S{power:.2f} F{_feed(speed)}"
            self.power = power
            self.speed = speed
        self.raw(words)
        self.cut_length += _distance(start, point)

    def _close(self) -> None:
        self.raw("G1F1")
        self.raw(f"{self.options.laser_mode} S0")
        self.speed = 1.0
        self.power = 0.0
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

    widest = max([*options.backlash, *(config.backlashes or [0.0])])
    if widest > 0:
        # Any positive move longer than the dead band ends in the positive
        # state, wherever the machine was and whichever way it last moved.
        writer.blank()
        writer.comment("both axes into the positive state")
        reach = 2.0 * widest
        ox, oy = config.origin
        writer._rapid((ox, oy))
        writer._rapid((ox + reach, oy + reach))

    records: list[dict] = []
    for cell in layout.travel_order():
        writer.blank()
        lash = "" if cell.backlash is None else f" backlash={cell.backlash:g}"
        writer.comment(
            f"cell spot={cell.spot:g} power={cell.power:g} speed={cell.speed:g}"
            f"{lash} block={cell.block} row={cell.row} col={cell.col}"
        )
        if cell.backlash is not None:
            writer.retune((cell.backlash, cell.backlash))
        before = writer.cut_length
        for path in cell.paths:
            writer.path(path, cell.power, cell.speed)
        records.append(_record(cell, writer.cut_length - before))

    if layout.label_paths:
        writer.blank()
        writer.comment(
            f"labels power={layout.label_power:g} speed={layout.label_speed:g}"
        )
        # Labels are read, not measured, so they run on the job's own value.
        writer.retune(options.backlash)
        for path in layout.label_paths:
            writer.path(path, layout.label_power, layout.label_speed)

    writer.blank()
    for line in options.postamble:
        writer.raw(line)
    writer.raw("M5          ; Switch tool off")
    if options.return_home:
        writer._rapid(config.origin)

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
        options=options,
    )


@dataclass(frozen=True)
class PathGroup:
    """A run of paths that share one power and feed, such as an isolation pass."""

    label: str
    paths: list[Polyline]
    power: float
    speed: float


def generate_paths(
    groups: list[PathGroup], options: GcodeOptions, header: list[str]
) -> Job:
    """Emit ready made paths, for jobs whose geometry comes from a file."""
    writer = _Writer(options)

    writer.raw("G21         ; Set units to mm")
    writer.raw("G90         ; Absolute positioning")
    writer.raw("G1F1")
    writer.raw(f"{options.laser_mode} S0")
    for line in options.preamble:
        writer.raw(line)
    writer.blank()
    for line in header:
        writer.comment(line)

    if writer.motion.active:
        writer.blank()
        writer.comment("both axes into the positive state")
        reach = 2.0 * max(options.backlash)
        ox, oy = options.origin
        writer._rapid((ox, oy))
        writer._rapid((ox + reach, oy + reach))

    records: list[dict] = []
    for group in groups:
        if not group.paths:
            continue
        writer.blank()
        writer.comment(
            f"{group.label}: {len(group.paths)} paths"
            f" power={group.power:g} speed={group.speed:g}"
        )
        before = writer.cut_length
        for path in group.paths:
            writer.path(path, group.power, group.speed)
        length = writer.cut_length - before
        records.append(
            {
                "label": group.label,
                "paths": len(group.paths),
                "power": group.power,
                "speed": group.speed,
                "length": length,
                "seconds": length / group.speed * 60.0 if group.speed else 0.0,
            }
        )

    writer.blank()
    for line in options.postamble:
        writer.raw(line)
    writer.raw("M5          ; Switch tool off")
    if options.return_home:
        writer._rapid(options.origin)

    cut_seconds = sum(record["seconds"] for record in records)
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
        options=options,
    )


def _record(cell: Cell, length: float) -> dict:
    return {
        "block": cell.block,
        "row": cell.row,
        "col": cell.col,
        "spot": cell.spot,
        "power": cell.power,
        "speed": cell.speed,
        "backlash": cell.backlash,
        "x": cell.center[0],
        "y": cell.center[1],
        "width": cell.width,
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
    lines = [
        "Laser parameter sweep",
        f"Power (S):    {_values(config.powers)} of {options.s_max:g}",
        f"Speed (F):    {_values(config.speeds)} mm/min",
        f"Spot size:    {_values(config.spots)} mm",
        _shape_comment(config),
        f"Grid:         {len(config.speeds)} speed x {len(config.powers)} power"
        f" x {len(config.spots)} spot = {len(layout.cells)} cells",
        f"Extent:       X {min_x:.2f} to {max_x:.2f}, Y {min_y:.2f} to {max_y:.2f} mm",
        "Labels:       left P=power, bottom F=speed, top d=spot"
        + (", b=backlash" if config.backlashes else ""),
    ]
    if options.approach > 0:
        lines.append(
            f"Approach:     {options.approach:g} mm below left of every path"
            " start, rapid in with both axes positive"
        )
    if config.backlashes:
        lines.append(
            f"Backlash:     swept {_values(config.backlashes)} mm, one block each,"
            " both axes, crossed with the beam off at every reversal"
        )
    elif any(options.backlash):
        bx, by = options.backlash
        lines.append(
            f"Backlash:     X {bx:g} mm, Y {by:g} mm, crossed with the beam off"
            " at every reversal"
        )
    lines.append("")
    return lines


def _sized(count: float | None, fixed: float, resolved: list[float]) -> str:
    """Either a fixed millimetre size or a spot multiple with what it works out as."""
    if count is None:
        return f"{fixed:g} mm"
    values = ", ".join(f"{value:g}" for value in resolved)
    return f"{count:g} spots ({values} mm)"


def _shape_comment(config) -> str:
    width = _sized(
        config.line_spots,
        config.line_width,
        [line_width_for(config, spot) for spot in config.spots],
    )
    if config.invert:
        border = _sized(
            config.border_spots,
            config.border,
            [border_for(config, spot) for spot in config.spots],
        )
        return (
            f"Island:       {config.line_length:g} mm x {width} left standing,"
            f" border {border}, loops {config.frame_order},"
            f" overlap {config.overlap:g}, passes {config.passes}"
        )
    return (
        f"Line:         {config.line_length:g} mm x {width},"
        f" fill {config.fill}, overlap {config.overlap:g}, passes {config.passes}"
    )


def _values(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values)
