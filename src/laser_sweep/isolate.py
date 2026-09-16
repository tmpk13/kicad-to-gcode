"""Isolation toolpaths: the loops that free every copper island from its neighbours.

The first loop runs half a spot outside the copper, so the beam edge stops on
the copper edge and the trace keeps its drawn width. Further loops step
outwards by the spot, widening the trench.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom
from .geom import Polygons
from .layout import INSIDE_OUT, OUTSIDE_IN, Point, Polyline, frame_offsets, path_length

MIRROR_NONE, MIRROR_X, MIRROR_Y = "none", "x", "y"
ANCHOR_CORNER, ANCHOR_CENTER, ANCHOR_KEEP = "corner", "center", "keep"

# How many bridged pairs to name before summarising the rest.
BRIDGE_REPORT = 8


@dataclass(frozen=True)
class IsoConfig:
    spot: float
    passes: int = 1
    clear: float | None = None
    overlap: float = 0.0
    margin: float = 0.0
    order: str = INSIDE_OUT
    mirror: str = MIRROR_NONE
    anchor: str = ANCHOR_CORNER
    origin: Point = (0.0, 0.0)


@dataclass(frozen=True)
class Loop:
    points: Polyline
    index: int
    offset: float

    @property
    def length(self) -> float:
        return path_length(self.points)


@dataclass(frozen=True)
class Bridge:
    """Copper islands that the beam cannot get between."""

    at: Point
    clearance: float
    islands: int = 2


@dataclass
class IsoPlan:
    config: IsoConfig
    copper: Polygons
    loops: list[Loop]
    offsets: list[float]
    islands: int
    bridges: list[Bridge] = field(default_factory=list)
    bridged_total: int = 0
    outline: list[Polyline] = field(default_factory=list)
    drills: list[Polyline] = field(default_factory=list)
    drill_holes: int = 0

    @property
    def cut_length(self) -> float:
        return sum(loop.length for loop in self.loops) + sum(
            path_length(path) for path in self.outline + self.drills
        )

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Everything the job touches, beam width included."""
        boxes = [geom.bounds(self.copper)] if self.copper else []
        for path in [loop.points for loop in self.loops] + self.outline + self.drills:
            xs = [x for x, _ in path]
            ys = [y for _, y in path]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        if not boxes:
            return (0.0, 0.0, 0.0, 0.0)
        radius = self.config.spot / 2.0
        return (
            min(box[0] for box in boxes) - radius,
            min(box[1] for box in boxes) - radius,
            max(box[2] for box in boxes) + radius,
            max(box[3] for box in boxes) + radius,
        )


def offsets_for(config: IsoConfig) -> list[float]:
    """Distance from the copper edge to each isolation loop.

    `clear` asks for a cleared band of a given width and works out the loops,
    the way the sweep's inverted frame does. Otherwise a pass count steps
    outwards by one spot at a time.
    """
    spot = config.spot
    if spot <= 0:
        raise ValueError("spot must be > 0")
    if not 0.0 <= config.overlap < 1.0:
        raise ValueError("overlap must be in [0, 1)")
    if config.margin < 0:
        raise ValueError("margin must be >= 0")
    if config.clear is not None:
        if config.clear <= 0:
            raise ValueError("clear must be > 0")
        offsets = frame_offsets(config.clear, spot, config.overlap)
    else:
        if config.passes < 1:
            raise ValueError("passes must be >= 1")
        step = spot * (1.0 - config.overlap)
        offsets = [spot / 2.0 + index * step for index in range(config.passes)]
    return [config.margin + offset for offset in offsets]


def transform(paths: Polygons, config: IsoConfig, shift: Point) -> Polygons:
    flip = -1.0 if config.mirror == MIRROR_X else 1.0
    flop = -1.0 if config.mirror == MIRROR_Y else 1.0
    dx, dy = shift
    return [[(x * flip + dx, y * flop + dy) for x, y in path] for path in paths]


def placement(
    copper: Polygons,
    config: IsoConfig,
    reach: float,
    extras: list[tuple[list, float]] | None = None,
) -> Point:
    """Where the mirrored gerber has to move for the anchor to hold.

    Everything the job touches counts, not just the copper: an outline cut or
    a drill mark outside the copper would otherwise push the job off the
    corner it was anchored to.
    """
    if config.anchor == ANCHOR_KEEP:
        return config.origin
    boxes = [_grown(transform(copper, config, (0.0, 0.0)), reach)]
    for paths, grow in extras or []:
        if paths:
            boxes.append(_grown(transform(paths, config, (0.0, 0.0)), grow))
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[2] for box in boxes)
    max_y = max(box[3] for box in boxes)
    if config.anchor == ANCHOR_CORNER:
        return (config.origin[0] - min_x, config.origin[1] - min_y)
    if config.anchor == ANCHOR_CENTER:
        return (
            config.origin[0] - (min_x + max_x) / 2.0,
            config.origin[1] - (min_y + max_y) / 2.0,
        )
    raise ValueError(f"unknown anchor {config.anchor!r}")


def _grown(paths, reach: float) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = geom.bounds(paths)
    return (min_x - reach, min_y - reach, max_x + reach, max_y + reach)


def signed_area(contour) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(contour, list(contour[1:]) + [contour[0]]):
        total += x0 * y1 - x1 * y0
    return total / 2.0


def as_loop(contour) -> Polyline:
    """A closed polyline that starts at the lowest left corner and runs +X.

    Every loop then leaves its start with X increasing and meets its first Y
    move going up, so backlash stays out of the two sides nearest the copper
    whenever the job is cut with an approach move.
    """
    points = list(contour)
    if signed_area(points) < 0:
        points.reverse()
    start = min(range(len(points)), key=lambda i: (points[i][1], points[i][0]))
    points = points[start:] + points[:start]
    points.append(points[0])
    return points


def loops_for(copper: Polygons, offsets: list[float]) -> list[Loop]:
    loops: list[Loop] = []
    for index, distance in enumerate(offsets):
        for contour in geom.offset(copper, distance):
            if len(contour) >= 3:
                loops.append(Loop(as_loop(contour), index, distance))
    return loops


def order_loops(loops: list[Loop], start: Point = (0.0, 0.0)) -> list[Loop]:
    """Greedy nearest neighbour, keeping the pass order intact."""
    ordered: list[Loop] = []
    position = start
    for index in sorted({loop.index for loop in loops}):
        remaining = [loop for loop in loops if loop.index == index]
        while remaining:
            nearest = min(remaining, key=lambda loop: math.dist(position, loop.points[0]))
            remaining.remove(nearest)
            ordered.append(nearest)
            position = nearest.points[-1]
    return ordered


def find_bridges(copper: Polygons, distance: float, limit: int = BRIDGE_REPORT):
    """Copper the first loop cannot get between, and how close it is.

    Islands closer together than twice the loop offset grow into one another,
    so the beam never parts them and they stay one net after the job runs.
    Comparing island counts before and after the offset finds that directly,
    including the case where the two growths only touch.
    """
    pieces = geom.islands(copper)
    if len(pieces) < 2:
        return [], 0
    grown = geom.islands(geom.offset(copper, distance))
    if len(grown) >= len(pieces):
        return [], 0

    groups: dict[int, list[Polygons]] = {}
    for piece in pieces:
        point = piece[0][0]
        for index, region in enumerate(grown):
            if geom.contains(region, point):
                groups.setdefault(index, []).append(piece)
                break

    found: list[Bridge] = []
    total = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        total += 1
        if len(found) < limit:
            flat = [contour for piece in members for contour in piece]
            x0, y0, x1, y1 = geom.bounds(flat)
            found.append(
                Bridge(
                    at=((x0 + x1) / 2.0, (y0 + y1) / 2.0),
                    clearance=_clearance(members, distance),
                    islands=len(members),
                )
            )
    found.sort(key=lambda bridge: bridge.clearance)
    return found, total


def _clearance(members: list[Polygons], reach: float) -> float:
    """The gap in a bridged group: twice the widest offset that still parts it."""
    flat = [contour for piece in members for contour in piece]
    low, high = 0.0, reach
    for _ in range(12):
        middle = (low + high) / 2.0
        if len(geom.islands(geom.offset(flat, middle))) < len(members):
            high = middle
        else:
            low = middle
    return 2.0 * low


def build(
    copper: Polygons,
    config: IsoConfig,
    outline: list[Polyline] | None = None,
    drills: list[Polyline] | None = None,
    drill_holes: int = 0,
    check_bridges: bool = True,
) -> IsoPlan:
    """Place the layer, cut the loops and report copper the beam cannot part."""
    offsets = offsets_for(config)
    reach = max(offsets) + config.spot / 2.0
    radius = config.spot / 2.0
    shift = placement(
        copper,
        config,
        reach,
        [(outline or [], radius), (drills or [], radius)],
    )
    placed = transform(copper, config, shift)
    order = list(offsets)
    if config.order == OUTSIDE_IN:
        order = list(reversed(order))
    elif config.order != INSIDE_OUT:
        raise ValueError(f"unknown order {config.order!r}")

    loops = loops_for(placed, order)
    loops = order_loops(loops)

    pieces = geom.islands(placed)
    bridges: list[Bridge] = []
    bridged = 0
    if check_bridges and pieces:
        bridges, bridged = find_bridges(placed, offsets[0])

    return IsoPlan(
        config=config,
        copper=placed,
        loops=loops,
        offsets=offsets,
        islands=len(pieces),
        bridges=bridges,
        bridged_total=bridged,
        outline=[list(path) for path in transform(outline or [], config, shift)],
        drills=[list(path) for path in transform(drills or [], config, shift)],
        drill_holes=drill_holes,
    )
