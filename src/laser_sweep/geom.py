"""Polygon sets: turning gerber figures into copper, and copper into offsets.

Everything here is a `Polygons`: a flat list of closed contours in millimetres
whose winding says what it is, counterclockwise for solid and clockwise for a
hole. That is the convention Clipper works in, so a result can be fed straight
back into the next operation.
"""

from __future__ import annotations

import pyclipper

from .gerber import CURVE_TOLERANCE, Draw, Fill, Image, Point

Contour = list[Point]
Polygons = list[Contour]

# Clipper works in integers; 1 unit is a nanometre, which keeps a 300 mm bed
# five orders of magnitude away from anything int64 minds.
SCALE = 1_000_000.0
ARC_TOLERANCE = CURVE_TOLERANCE * SCALE
MITER_LIMIT = 2.0

ROUND, SQUARE, BUTT = "round", "square", "butt"

# Gaps this small are welded shut before copper is split into islands: it is a
# quarter of the 4 micron step of the machine this was written for.
WELD = 0.001

_OPEN_CAPS = {
    ROUND: pyclipper.ET_OPENROUND,
    SQUARE: pyclipper.ET_OPENSQUARE,
    BUTT: pyclipper.ET_OPENBUTT,
}


def _up(paths) -> list:
    return [[(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in path] for path in paths]


def _down(paths) -> Polygons:
    return [[(x / SCALE, y / SCALE) for x, y in path] for path in paths]


def _clip(operation: int, subject: Polygons, clip: Polygons) -> Polygons:
    if not subject:
        return [] if operation != pyclipper.CT_UNION else [list(c) for c in clip]
    if not clip:
        return [list(c) for c in subject]
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(_up(subject), pyclipper.PT_SUBJECT, True)
    clipper.AddPaths(_up(clip), pyclipper.PT_CLIP, True)
    return _down(
        clipper.Execute(operation, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    )


def union(subject: Polygons, clip: Polygons | None = None) -> Polygons:
    """Union two sets, or self-union one to resolve overlaps and windings."""
    if clip is None:
        if not subject:
            return []
        clipper = pyclipper.Pyclipper()
        clipper.AddPaths(_up(subject), pyclipper.PT_SUBJECT, True)
        return _down(
            clipper.Execute(
                pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
            )
        )
    return _clip(pyclipper.CT_UNION, subject, clip)


def difference(subject: Polygons, clip: Polygons) -> Polygons:
    return _clip(pyclipper.CT_DIFFERENCE, subject, clip)


def intersection(subject: Polygons, clip: Polygons) -> Polygons:
    return _clip(pyclipper.CT_INTERSECTION, subject, clip)


def offset(paths: Polygons, delta: float, join: str = ROUND) -> Polygons:
    """Grow (or shrink, with a negative delta) a closed polygon set."""
    if not paths or delta == 0.0:
        return [list(path) for path in paths]
    engine = pyclipper.PyclipperOffset(MITER_LIMIT, ARC_TOLERANCE)
    style = pyclipper.JT_ROUND if join == ROUND else pyclipper.JT_MITER
    engine.AddPaths(_up(paths), style, pyclipper.ET_CLOSEDPOLYGON)
    return _down(engine.Execute(delta * SCALE))


def stroke(paths: list, width: float, cap: str = ROUND) -> Polygons:
    """The area a round or square pen of `width` covers along open paths."""
    if not paths or width <= 0:
        return []
    engine = pyclipper.PyclipperOffset(MITER_LIMIT, ARC_TOLERANCE)
    engine.AddPaths(_up(paths), pyclipper.JT_ROUND, _OPEN_CAPS[cap])
    return _down(engine.Execute(width / 2.0 * SCALE))


def sweep(path: list, outline: Contour) -> Polygons:
    """The area a convex aperture covers along an open path.

    That is the Minkowski sum of the aperture and the path, which is what the
    deprecated rectangular draws mean.
    """
    if not path:
        return []
    points = list(path) if len(path) > 1 else [path[0], path[0]]
    pattern = _up([list(outline)])[0]
    if not pyclipper.Orientation(pattern):
        pattern = pyclipper.ReversePath(pattern)
    pieces = pyclipper.MinkowskiSum(pattern, _up([points])[0], False)
    return union(_down(pieces))


def area(paths: Polygons) -> float:
    return sum(pyclipper.Area(_up([path])[0]) for path in paths) / (SCALE * SCALE)


def bounds(paths: Polygons) -> tuple[float, float, float, float]:
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def weld(paths: Polygons, gap: float = WELD) -> Polygons:
    """Close hairline gaps so copper that only touches counts as connected.

    Clipper keeps two shapes that meet at a single corner as two contours. On
    the board they are one piece of copper, so grow and shrink the set to join
    them. The default is a quarter of the machine's step, well under anything
    a beam could cut apart.
    """
    if not paths:
        return []
    return offset(offset(paths, gap), -gap)


def islands(paths: Polygons) -> list[Polygons]:
    """Split a polygon set into connected pieces, each an outline plus holes."""
    if not paths:
        return []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(_up(weld(paths)), pyclipper.PT_SUBJECT, True)
    tree = clipper.Execute2(
        pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
    )
    pieces: list[Polygons] = []

    def walk(node) -> None:
        for child in node.Childs:
            # A child of the root (or of a hole) is solid; its own children are
            # the holes in it, and anything inside those is a separate island.
            piece = [[(x / SCALE, y / SCALE) for x, y in child.Contour]]
            for hole in child.Childs:
                piece.append([(x / SCALE, y / SCALE) for x, y in hole.Contour])
                walk(hole)
            pieces.append(piece)

    walk(tree)
    return pieces


def copper(image: Image) -> Polygons:
    """Everything a layer paints, in order, with clear polarity taken out."""
    result: Polygons = []
    for dark, figures in _runs(image.figures):
        batch = _batch(figures)
        result = union(result, batch) if dark else difference(result, batch)
    return result


def _runs(figures: list) -> list[tuple[bool, list]]:
    """Group consecutive figures of the same polarity, order preserved."""
    runs: list[tuple[bool, list]] = []
    for figure in figures:
        if runs and runs[-1][0] == figure.dark:
            runs[-1][1].append(figure)
        else:
            runs.append((figure.dark, [figure]))
    return runs


def _batch(figures: list) -> Polygons:
    """One polarity run: strokes of a common width share an offset call."""
    plain: Polygons = []
    strokes: dict[tuple[float, str], list] = {}
    resolved: Polygons = []
    for figure in figures:
        if isinstance(figure, Fill):
            if all(exposure for exposure, _ in figure.parts):
                plain.extend(_solid(contour) for _, contour in figure.parts)
            else:
                resolved = union(resolved, _resolve(figure))
        elif isinstance(figure, Draw) and figure.width > 0:
            strokes.setdefault((figure.width, ROUND), []).append(list(figure.points))
        elif figure.outline is not None:
            resolved = union(resolved, sweep(list(figure.points), figure.outline))
    batch = union(plain) if plain else []
    for (width, cap), paths in strokes.items():
        batch = union(batch, stroke(paths, width, cap))
    return union(batch, resolved) if resolved else batch


def _solid(contour) -> Contour:
    """An exposed contour wound counterclockwise, whichever way it came.

    Aperture macros mix windings (a roundrect pad's side rectangles run
    clockwise), and under the nonzero rule a clockwise part laid over a
    counterclockwise one cancels to a hole in the middle of the pad.
    """
    points = list(contour)
    if pyclipper.Orientation(_up([points])[0]):
        return points
    return points[::-1]


def _resolve(fill: Fill) -> Polygons:
    """A flash whose aperture has holes, or re-exposes over them."""
    result: Polygons = []
    for exposure, contour in fill.parts:
        piece = union([list(contour)])
        result = union(result, piece) if exposure else difference(result, piece)
    return result


def contains(piece: Polygons, point: Point) -> bool:
    """Is `point` inside one island, holes taken out?

    `piece` is an island as `islands` returns it: the outline first, then its
    holes.
    """
    if not piece:
        return False
    scaled = (int(round(point[0] * SCALE)), int(round(point[1] * SCALE)))
    if pyclipper.PointInPolygon(scaled, _up([piece[0]])[0]) == 0:
        return False
    return all(
        pyclipper.PointInPolygon(scaled, _up([hole])[0]) == 0 for hole in piece[1:]
    )
