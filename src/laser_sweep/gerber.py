"""RS-274X gerber reader: a copper layer as filled and stroked figures, in mm.

The reader resolves apertures and aperture macros into plain contours and
leaves every boolean operation to `geom`, so nothing here needs a polygon
clipper. Coordinates come out in millimetres with Y up, as the gerber file
means them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

Point = tuple[float, float]
Contour = tuple[Point, ...]

MM_PER_INCH = 25.4
# Largest distance a chord may fall inside the curve it replaces, in mm.
CURVE_TOLERANCE = 0.002
MIN_SEGMENTS = 8


class GerberError(ValueError):
    """The file is not gerber this reader can follow."""


@dataclass(frozen=True)
class Fill:
    """Contours applied in order: exposure on adds, exposure off cuts a hole.

    A flashed pad, a region and a polygon aperture are all this shape. The
    order matters because an aperture macro may re-expose over a hole.
    """

    parts: tuple[tuple[bool, Contour], ...]
    dark: bool = True


@dataclass(frozen=True)
class Draw:
    """An open path stroked with an aperture.

    `width` strokes with a round aperture, which is the only kind the current
    gerber spec allows. `outline` carries a convex aperture for the deprecated
    rectangular draws, swept as the hull of its copies at both segment ends.
    """

    points: tuple[Point, ...]
    width: float = 0.0
    outline: Contour | None = None
    dark: bool = True


Figure = Fill | Draw


@dataclass
class Image:
    """Everything a layer draws, in the order it draws it."""

    figures: list[Figure] = field(default_factory=list)
    file_function: str = ""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for figure in self.figures:
            if isinstance(figure, Fill):
                points = [p for _, contour in figure.parts for p in contour]
                grow = 0.0
            else:
                points = list(figure.points)
                grow = figure.width / 2.0
                if figure.outline:
                    grow = max(max(abs(x), abs(y)) for x, y in figure.outline)
            xs.extend(p[0] - grow for p in points)
            xs.extend(p[0] + grow for p in points)
            ys.extend(p[1] - grow for p in points)
            ys.extend(p[1] + grow for p in points)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))


def circle_points(center: Point, radius: float, tolerance: float = CURVE_TOLERANCE) -> Contour:
    """A closed polygon inscribing a circle, sagitta under `tolerance`."""
    if radius <= 0:
        return ()
    count = segments_for(radius, 2.0 * math.pi, tolerance)
    cx, cy = center
    step = 2.0 * math.pi / count
    return tuple(
        (cx + radius * math.cos(i * step), cy + radius * math.sin(i * step))
        for i in range(count)
    )


def segments_for(radius: float, sweep: float, tolerance: float = CURVE_TOLERANCE) -> int:
    """Segment count for `sweep` radians of a circle at `radius`."""
    sweep = abs(sweep)
    if radius <= tolerance:
        return MIN_SEGMENTS
    # Sagitta of a chord spanning t radians is r*(1 - cos(t/2)).
    step = 2.0 * math.acos(max(-1.0, 1.0 - tolerance / radius))
    return max(MIN_SEGMENTS, int(math.ceil(sweep / step)))


def arc_points(
    start: Point, end: Point, center: Point, clockwise: bool, full: bool = False
) -> list[Point]:
    """Points along an arc, `start` excluded and `end` included."""
    radius = math.dist(start, center)
    begin = math.atan2(start[1] - center[1], start[0] - center[0])
    finish = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = finish - begin
    if clockwise:
        while sweep > 0:
            sweep -= 2.0 * math.pi
        if full or abs(sweep) < 1e-12:
            sweep = -2.0 * math.pi
    else:
        while sweep < 0:
            sweep += 2.0 * math.pi
        if full or abs(sweep) < 1e-12:
            sweep = 2.0 * math.pi
    count = segments_for(radius, sweep)
    points = []
    for i in range(1, count + 1):
        angle = begin + sweep * i / count
        points.append(
            (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
        )
    points[-1] = end
    return points


def rotate(contour: Contour, degrees: float) -> Contour:
    if not degrees:
        return contour
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return tuple((x * cos - y * sin, x * sin + y * cos) for x, y in contour)


def translate(contour: Contour, offset: Point) -> Contour:
    dx, dy = offset
    return tuple((x + dx, y + dy) for x, y in contour)


def rect_points(width: float, height: float, center: Point = (0.0, 0.0)) -> Contour:
    x, y = center
    hw, hh = width / 2.0, height / 2.0
    return (
        (x - hw, y - hh),
        (x + hw, y - hh),
        (x + hw, y + hh),
        (x - hw, y + hh),
    )


def obround_points(
    width: float,
    height: float,
    center: Point = (0.0, 0.0),
    tolerance: float = CURVE_TOLERANCE,
) -> Contour:
    """A rectangle with the shorter pair of sides replaced by semicircles."""
    x, y = center
    if width > height:
        radius = height / 2.0
        span = (width - height) / 2.0
        ends = [(x + span, y), (x - span, y)]
        starts = [-math.pi / 2.0, math.pi / 2.0]
    else:
        radius = width / 2.0
        span = (height - width) / 2.0
        ends = [(x, y + span), (x, y - span)]
        starts = [0.0, math.pi]
    count = max(MIN_SEGMENTS // 2, segments_for(radius, math.pi, tolerance) // 2 + 1)
    points: list[Point] = []
    for (cx, cy), begin in zip(ends, starts):
        for i in range(count + 1):
            angle = begin + math.pi * i / count
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return tuple(points)


def polygon_points(diameter: float, vertices: int, degrees: float = 0.0) -> Contour:
    radius = diameter / 2.0
    angle = math.radians(degrees)
    return tuple(
        (
            radius * math.cos(angle + 2.0 * math.pi * i / vertices),
            radius * math.sin(angle + 2.0 * math.pi * i / vertices),
        )
        for i in range(vertices)
    )


@dataclass(frozen=True)
class Aperture:
    """A resolved aperture: what it flashes and what it can stroke with."""

    code: int
    parts: tuple[tuple[bool, Contour], ...]
    outline: Contour | None = None
    diameter: float = 0.0
    name: str = ""

    def flash(self, at: Point) -> Fill:
        return Fill(tuple((on, translate(c, at)) for on, c in self.parts))


# --- aperture macros -------------------------------------------------------

_TOKEN = re.compile(r"\s*(\$\d+|[-+]?(?:\d+\.?\d*|\.\d+)|[-+xX/()])")


class _Expression:
    """Macro arithmetic: numbers, $n, + - x / and parentheses."""

    def __init__(self, text: str, variables: dict[int, float]) -> None:
        self.tokens: list[str] = []
        pos = 0
        while pos < len(text):
            match = _TOKEN.match(text, pos)
            if not match:
                raise GerberError(f"macro: cannot read {text[pos:]!r}")
            self.tokens.append(match.group(1))
            pos = match.end()
        self.index = 0
        self.variables = variables

    def value(self) -> float:
        result = self._sum()
        if self.index != len(self.tokens):
            raise GerberError(f"macro: trailing {self.tokens[self.index:]!r}")
        return result

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _sum(self) -> float:
        result = self._product()
        while self._peek() in ("+", "-"):
            operator = self.tokens[self.index]
            self.index += 1
            right = self._product()
            result = result + right if operator == "+" else result - right
        return result

    def _product(self) -> float:
        result = self._unary()
        while self._peek() in ("x", "X", "/"):
            operator = self.tokens[self.index]
            self.index += 1
            right = self._unary()
            if operator == "/":
                if right == 0:
                    raise GerberError("macro: division by zero")
                result /= right
            else:
                result *= right
        return result

    def _unary(self) -> float:
        token = self._peek()
        if token in ("+", "-"):
            self.index += 1
            value = self._unary()
            return value if token == "+" else -value
        return self._atom()

    def _atom(self) -> float:
        token = self._peek()
        if token is None:
            raise GerberError("macro: expression ended early")
        self.index += 1
        if token == "(":
            value = self._sum()
            if self._peek() != ")":
                raise GerberError("macro: unbalanced (")
            self.index += 1
            return value
        if token.startswith("$"):
            return self.variables.get(int(token[1:]), 0.0)
        return float(token)


def _evaluate(text: str, variables: dict[int, float]) -> float:
    return _Expression(text, variables).value()


def expand_macro(
    body: list[str], arguments: list[float], tolerance: float = CURVE_TOLERANCE
) -> tuple[tuple[bool, Contour], ...]:
    """Run a macro body with its flash arguments into exposure-tagged contours.

    `tolerance` is in the units the macro is written in, which are the file's
    own: apertures are resolved before the unit scale is applied, so that a
    rotation argument is never mistaken for a length.
    """
    variables = {i + 1: value for i, value in enumerate(arguments)}
    parts: list[tuple[bool, Contour]] = []
    for statement in body:
        statement = statement.strip()
        if not statement or statement.startswith("0"):
            continue
        if statement.startswith("$"):
            name, _, expression = statement.partition("=")
            variables[int(name[1:])] = _evaluate(expression, variables)
            continue
        fields = statement.split(",")
        code = int(float(fields[0]))
        values = [_evaluate(field, variables) for field in fields[1:]]
        parts.extend(_primitive(code, values, tolerance))
    return tuple(parts)


def _primitive(
    code: int, values: list[float], tolerance: float = CURVE_TOLERANCE
) -> list[tuple[bool, Contour]]:
    def spin(contour: Contour, degrees: float) -> Contour:
        return rotate(contour, degrees)

    if code == 1:  # circle
        exposure, diameter, x, y = values[0], values[1], values[2], values[3]
        turn = values[4] if len(values) > 4 else 0.0
        return [
            (exposure != 0, spin(circle_points((x, y), diameter / 2.0, tolerance), turn))
        ]
    if code in (2, 20):  # vector line
        exposure, width = values[0], values[1]
        start, end = (values[2], values[3]), (values[4], values[5])
        turn = values[6] if len(values) > 6 else 0.0
        return [(exposure != 0, spin(_vector_line(start, end, width), turn))]
    if code == 21:  # center line
        exposure, width, height = values[0], values[1], values[2]
        center = (values[3], values[4])
        turn = values[5] if len(values) > 5 else 0.0
        return [(exposure != 0, spin(rect_points(width, height, center), turn))]
    if code == 22:  # lower left line
        exposure, width, height = values[0], values[1], values[2]
        center = (values[3] + width / 2.0, values[4] + height / 2.0)
        turn = values[5] if len(values) > 5 else 0.0
        return [(exposure != 0, spin(rect_points(width, height, center), turn))]
    if code == 4:  # outline
        exposure, count = values[0], int(values[1])
        coordinates = values[2 : 2 + 2 * (count + 1)]
        turn = values[2 + 2 * (count + 1)] if len(values) > 2 + 2 * (count + 1) else 0.0
        points = tuple(
            (coordinates[i], coordinates[i + 1]) for i in range(0, len(coordinates), 2)
        )
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        return [(exposure != 0, spin(points, turn))]
    if code == 5:  # regular polygon
        exposure, count = values[0], int(values[1])
        center, diameter = (values[2], values[3]), values[4]
        turn = values[5] if len(values) > 5 else 0.0
        contour = translate(polygon_points(diameter, count), center)
        return [(exposure != 0, spin(contour, turn))]
    if code == 6:  # moire
        return _moire(values, tolerance)
    if code == 7:  # thermal
        return _thermal(values, tolerance)
    raise GerberError(f"macro primitive {code} is not supported")


def _vector_line(start: Point, end: Point, width: float) -> Contour:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return rect_points(width, width, start)
    nx, ny = -dy / length * width / 2.0, dx / length * width / 2.0
    return (
        (start[0] + nx, start[1] + ny),
        (end[0] + nx, end[1] + ny),
        (end[0] - nx, end[1] - ny),
        (start[0] - nx, start[1] - ny),
    )


def _moire(values: list[float], tolerance: float) -> list[tuple[bool, Contour]]:
    center = (values[0], values[1])
    outer, thickness, gap = values[2], values[3], values[4]
    rings = int(values[5])
    cross_thickness, cross_length = values[6], values[7]
    turn = values[8] if len(values) > 8 else 0.0
    parts: list[tuple[bool, Contour]] = []
    radius = outer / 2.0
    for _ in range(rings):
        if radius <= 0:
            break
        parts.append((True, rotate(circle_points(center, radius, tolerance), turn)))
        inner = radius - thickness
        if inner > 0:
            parts.append((False, rotate(circle_points(center, inner, tolerance), turn)))
        radius = inner - gap
    if cross_thickness > 0 and cross_length > 0:
        parts.append(
            (True, rotate(rect_points(cross_length, cross_thickness, center), turn))
        )
        parts.append(
            (True, rotate(rect_points(cross_thickness, cross_length, center), turn))
        )
    return parts


def _thermal(values: list[float], tolerance: float) -> list[tuple[bool, Contour]]:
    center = (values[0], values[1])
    outer, inner, gap = values[2], values[3], values[4]
    turn = values[5] if len(values) > 5 else 0.0
    parts = [
        (True, rotate(circle_points(center, outer / 2.0, tolerance), turn)),
        (False, rotate(circle_points(center, inner / 2.0, tolerance), turn)),
    ]
    reach = outer * 1.1
    parts.append((False, rotate(rect_points(reach, gap, center), turn)))
    parts.append((False, rotate(rect_points(gap, reach, center), turn)))
    return parts


# --- standard apertures ----------------------------------------------------


def standard_aperture(
    code: int, template: str, values: list[float], tolerance: float = CURVE_TOLERANCE
) -> Aperture:
    """C, R, O and P, each with an optional round hole.

    Sizes are in the file's units. Only the lengths would scale to mm, never
    the rotation a polygon carries, so the whole aperture is scaled afterwards
    instead.
    """
    if template == "C":
        diameter = values[0]
        outline = circle_points((0.0, 0.0), diameter / 2.0, tolerance)
        parts = [(True, outline)]
        hole = values[1] if len(values) > 1 else 0.0
        return _with_hole(code, parts, outline, hole, template, diameter, tolerance)
    if template in ("R", "O"):
        width, height = values[0], values[1]
        outline = (
            rect_points(width, height)
            if template == "R"
            else obround_points(width, height, tolerance=tolerance)
        )
        hole = values[2] if len(values) > 2 else 0.0
        return _with_hole(code, [(True, outline)], outline, hole, template, 0.0, tolerance)
    if template == "P":
        diameter, vertices = values[0], int(values[1])
        if vertices < 3:
            raise GerberError(f"aperture D{code}: a polygon needs 3 or more vertices")
        turn = values[2] if len(values) > 2 else 0.0
        outline = polygon_points(diameter, vertices, turn)
        hole = values[3] if len(values) > 3 else 0.0
        return _with_hole(code, [(True, outline)], outline, hole, template, 0.0, tolerance)
    raise GerberError(f"aperture D{code}: template {template!r} is not supported")


def _with_hole(
    code: int,
    parts: list[tuple[bool, Contour]],
    outline: Contour,
    hole: float,
    name: str,
    diameter: float = 0.0,
    tolerance: float = CURVE_TOLERANCE,
) -> Aperture:
    if hole > 0:
        parts = parts + [(False, circle_points((0.0, 0.0), hole / 2.0, tolerance))]
        # A holed aperture is not solid, so the spec forbids stroking with it
        # and there is nothing sensible to sweep along a line.
        return Aperture(code=code, parts=tuple(parts), name=name)
    return Aperture(
        code=code, parts=tuple(parts), outline=outline, diameter=diameter, name=name
    )


def _scale_aperture(aperture: Aperture, scale: float) -> Aperture:
    """File units to millimetres, once the shape is built."""
    if scale == 1.0:
        return aperture
    def grow(contour: Contour) -> Contour:
        return tuple((x * scale, y * scale) for x, y in contour)

    return Aperture(
        code=aperture.code,
        parts=tuple((on, grow(contour)) for on, contour in aperture.parts),
        outline=grow(aperture.outline) if aperture.outline is not None else None,
        diameter=aperture.diameter * scale,
        name=aperture.name,
    )


# --- the file --------------------------------------------------------------

_WORD = re.compile(r"([A-Za-z])([+-]?[0-9]*\.?[0-9]*)")

LINEAR, CLOCKWISE, COUNTERCLOCKWISE = 1, 2, 3

_REFUSED = {
    "AB": "block apertures",
    "AS": "axis select",
    "IR": "image rotation",
    "MI": "mirror image",
    "OF": "image offset",
    "SF": "scale factor",
}


class _Reader:
    def __init__(self) -> None:
        self.image = Image()
        self.apertures: dict[int, Aperture] = {}
        self.macros: dict[str, list[str]] = {}
        self.aperture: Aperture | None = None
        self.scale = 1.0  # file units to mm
        self.integer_digits = 0
        self.decimal_digits = 6
        self.omit = "L"
        self.position: Point = (0.0, 0.0)
        self.mode = LINEAR
        self.multi_quadrant = True
        self.dark = True
        self.in_region = False
        self.region: list[list[Point]] = []
        self.stroke: list[Point] = []
        self.repeat: tuple[int, int, float, float] | None = None
        self.repeated: list[Figure] = []
        self.line = 0

    # -- emitting

    def add(self, figure: Figure) -> None:
        if self.repeat is not None:
            self.repeated.append(figure)
        else:
            self.image.figures.append(figure)

    def flush_stroke(self) -> None:
        if len(self.stroke) < 2:
            self.stroke = []
            return
        aperture = self.aperture
        if aperture is None:
            raise GerberError("a draw before any aperture was selected")
        points = tuple(self.stroke)
        self.stroke = []
        if aperture.diameter > 0:
            self.add(Draw(points, width=aperture.diameter, dark=self.dark))
        elif aperture.outline is not None:
            self.add(Draw(points, outline=aperture.outline, dark=self.dark))
        else:
            raise GerberError(
                f"aperture D{aperture.code} ({aperture.name}) cannot stroke a line;"
                " only circles and the deprecated rectangular draws can"
            )

    def flush_repeat(self) -> None:
        if self.repeat is None:
            return
        columns, rows, step_x, step_y = self.repeat
        figures = self.repeated
        self.repeat = None
        self.repeated = []
        for row in range(rows):
            for column in range(columns):
                offset = (column * step_x, row * step_y)
                for figure in figures:
                    self.image.figures.append(_shift(figure, offset))

    # -- coordinates

    def coordinate(self, text: str) -> float:
        if "." in text:
            return float(text) * self.scale
        sign = 1.0
        if text and text[0] in "+-":
            sign = -1.0 if text[0] == "-" else 1.0
            text = text[1:]
        if not text:
            raise GerberError(f"line {self.line}: empty coordinate")
        if self.omit == "T":
            text = text.ljust(self.integer_digits + self.decimal_digits, "0")
        return sign * int(text) / (10.0**self.decimal_digits) * self.scale

    # -- commands

    def run(self, text: str) -> Image:
        for kind, body, line in _commands(text):
            self.line = line
            if kind == "ext":
                self.extended(body)
            else:
                self.function(body)
        self.flush_stroke()
        self.flush_repeat()
        return self.image

    def extended(self, body: str) -> None:
        statements = [part for part in body.split("*") if part.strip()]
        if not statements:
            return
        head = statements[0]
        code = head[:2]
        if code == "FS":
            self.format_spec(head)
        elif code == "MO":
            unit = head[2:].strip()
            if unit == "MM":
                self.scale = 1.0
            elif unit == "IN":
                self.scale = MM_PER_INCH
            else:
                raise GerberError(f"line {self.line}: unknown unit {unit!r}")
        elif code == "AD":
            self.define_aperture(head)
        elif code == "AM":
            self.macros[head[2:]] = statements[1:]
        elif code == "LP":
            self.flush_stroke()
            self.dark = head[2:].strip() != "C"
        elif code == "SR":
            self.step_repeat(head)
        elif code == "TF":
            if head.startswith("TF.FileFunction"):
                self.image.file_function = head.split(",", 1)[1] if "," in head else ""
        elif code in ("TA", "TO", "TD", "LN", "IN"):
            pass
        elif code == "IP":
            if head[2:].strip() not in ("POS", ""):
                raise GerberError(f"line {self.line}: negative images are not supported")
        elif code in _REFUSED:
            raise GerberError(f"line {self.line}: {_REFUSED[code]} are not supported")
        else:
            raise GerberError(f"line {self.line}: unknown command %{head}%")

    def format_spec(self, head: str) -> None:
        match = re.match(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", head)
        if not match:
            raise GerberError(f"line {self.line}: cannot read format spec {head!r}")
        self.omit = match.group(1) or "L"
        if match.group(2) == "I":
            raise GerberError(
                f"line {self.line}: incremental coordinates are not supported"
            )
        self.integer_digits = int(match.group(3))
        self.decimal_digits = int(match.group(4))
        if (match.group(3), match.group(4)) != (match.group(5), match.group(6)):
            raise GerberError(f"line {self.line}: X and Y formats differ")

    def define_aperture(self, head: str) -> None:
        match = re.match(r"ADD(\d+)([A-Za-z_.$][A-Za-z0-9_.$-]*)(?:,(.*))?$", head)
        if not match:
            raise GerberError(f"line {self.line}: cannot read aperture {head!r}")
        code = int(match.group(1))
        template = match.group(2)
        raw = match.group(3) or ""
        values = [float(part) for part in raw.split("X") if part.strip()] if raw else []
        # Apertures are resolved in the file's own units and scaled afterwards:
        # some of their parameters are angles, which must not be scaled.
        tolerance = CURVE_TOLERANCE / self.scale
        if template in ("C", "R", "O", "P"):
            aperture = standard_aperture(code, template, values, tolerance)
        elif template in self.macros:
            parts = expand_macro(self.macros[template], values, tolerance)
            aperture = Aperture(code=code, parts=parts, name=template)
        else:
            raise GerberError(
                f"line {self.line}: aperture D{code} uses unknown template {template!r}"
            )
        self.apertures[code] = _scale_aperture(aperture, self.scale)

    def step_repeat(self, head: str) -> None:
        self.flush_stroke()
        self.flush_repeat()
        match = re.match(r"SRX(\d+)Y(\d+)I([-\d.]+)J([-\d.]+)", head)
        if not match:
            return  # %SR*% closes the block, which flush_repeat already did.
        self.repeat = (
            int(match.group(1)),
            int(match.group(2)),
            float(match.group(3)) * self.scale,
            float(match.group(4)) * self.scale,
        )

    def function(self, body: str) -> None:
        if body.startswith("G04") or body.startswith("G4"):
            return
        words = [(letter.upper(), value) for letter, value in _WORD.findall(body) if value != ""]
        if not words and body.strip():
            raise GerberError(f"line {self.line}: cannot read {body!r}")
        x = y = None
        i = j = None
        operation: int | None = None
        for letter, value in words:
            if letter == "G":
                self.g_code(int(float(value)))
            elif letter == "D":
                code = int(float(value))
                if code >= 10:
                    self.select(code)
                else:
                    operation = code
            elif letter == "X":
                x = self.coordinate(value)
            elif letter == "Y":
                y = self.coordinate(value)
            elif letter == "I":
                i = self.coordinate(value)
            elif letter == "J":
                j = self.coordinate(value)
            elif letter == "M":
                return
            elif letter in ("N", "*"):
                pass
            else:
                raise GerberError(f"line {self.line}: unknown word {letter}{value}")
        if operation is None:
            return
        point = (
            self.position[0] if x is None else x,
            self.position[1] if y is None else y,
        )
        self.operate(operation, point, i, j)

    def g_code(self, code: int) -> None:
        if code == 1:
            self.mode = LINEAR
        elif code == 2:
            self.mode = CLOCKWISE
        elif code == 3:
            self.mode = COUNTERCLOCKWISE
        elif code == 36:
            self.flush_stroke()
            self.in_region = True
            self.region = []
        elif code == 37:
            self.end_region()
        elif code == 74:
            self.multi_quadrant = False
        elif code == 75:
            self.multi_quadrant = True
        elif code == 70:
            self.scale = MM_PER_INCH
        elif code == 71:
            self.scale = 1.0
        elif code in (90, 54, 55):
            pass
        else:
            raise GerberError(f"line {self.line}: G{code:02d} is not supported")

    def select(self, code: int) -> None:
        if code not in self.apertures:
            raise GerberError(f"line {self.line}: aperture D{code} was never defined")
        if self.aperture is None or self.aperture.code != code:
            self.flush_stroke()
        self.aperture = self.apertures[code]

    def operate(self, code: int, point: Point, i: float | None, j: float | None) -> None:
        if code == 1:
            self.interpolate(point, i, j)
        elif code == 2:
            if self.in_region:
                self.close_contour()
                self.region.append([point])
            else:
                self.flush_stroke()
            self.position = point
        elif code == 3:
            if self.in_region:
                raise GerberError(f"line {self.line}: a flash inside a region")
            self.flush_stroke()
            if self.aperture is None:
                raise GerberError(f"line {self.line}: a flash before any aperture")
            flash = self.aperture.flash(point)
            self.add(Fill(flash.parts, dark=self.dark))
            self.position = point
        else:
            raise GerberError(f"line {self.line}: D{code:02d} is not an operation")

    def interpolate(self, point: Point, i: float | None, j: float | None) -> None:
        if self.mode == LINEAR:
            points = [point]
        else:
            points = self.arc(point, i or 0.0, j or 0.0)
        if self.in_region:
            if not self.region:
                self.region.append([self.position])
            self.region[-1].extend(points)
        else:
            if not self.stroke:
                self.stroke.append(self.position)
            self.stroke.extend(points)
        self.position = point

    def arc(self, point: Point, i: float, j: float) -> list[Point]:
        clockwise = self.mode == CLOCKWISE
        start = self.position
        if self.multi_quadrant:
            center = (start[0] + i, start[1] + j)
            full = math.dist(start, point) < 1e-9
            return arc_points(start, point, center, clockwise, full=full)
        best = None
        for si in (i, -i):
            for sj in (j, -j):
                center = (start[0] + si, start[1] + sj)
                error = abs(math.dist(start, center) - math.dist(point, center))
                if error > 1e-6:
                    continue
                candidate = arc_points(start, point, center, clockwise)
                sweep = _sweep(start, point, center, clockwise)
                if sweep > math.pi / 2.0 + 1e-6:
                    continue
                if best is None or sweep < best[0]:
                    best = (sweep, candidate)
        if best is None:
            raise GerberError(f"line {self.line}: cannot place a single quadrant arc")
        return best[1]

    def close_contour(self) -> None:
        if self.region and len(self.region[-1]) < 3:
            self.region.pop()

    def end_region(self) -> None:
        self.close_contour()
        contours = tuple(tuple(contour) for contour in self.region if len(contour) >= 3)
        if contours:
            self.add(Fill(tuple((True, contour) for contour in contours), dark=self.dark))
        self.region = []
        self.in_region = False


def _sweep(start: Point, end: Point, center: Point, clockwise: bool) -> float:
    begin = math.atan2(start[1] - center[1], start[0] - center[0])
    finish = math.atan2(end[1] - center[1], end[0] - center[0])
    delta = finish - begin
    if clockwise:
        while delta > 0:
            delta -= 2.0 * math.pi
    else:
        while delta < 0:
            delta += 2.0 * math.pi
    return abs(delta)


def _shift(figure: Figure, offset: Point) -> Figure:
    if isinstance(figure, Fill):
        return Fill(
            tuple((on, translate(contour, offset)) for on, contour in figure.parts),
            dark=figure.dark,
        )
    return Draw(
        translate(figure.points, offset),
        width=figure.width,
        outline=figure.outline,
        dark=figure.dark,
    )


def _commands(text: str):
    """Split a gerber file into extended blocks and function code blocks."""
    position = 0
    line = 1
    length = len(text)
    while position < length:
        character = text[position]
        if character.isspace():
            if character == "\n":
                line += 1
            position += 1
            continue
        if character == "%":
            end = text.find("%", position + 1)
            if end < 0:
                raise GerberError(f"line {line}: unterminated % block")
            body = text[position + 1 : end]
            yield "ext", body, line
            line += body.count("\n")
            position = end + 1
        else:
            end = text.find("*", position)
            if end < 0:
                raise GerberError(f"line {line}: a command with no *")
            body = text[position:end]
            yield "fn", body.strip(), line
            line += body.count("\n")
            position = end + 1


def parse(text: str) -> Image:
    """Read a gerber file into figures, in millimetres."""
    return _Reader().run(text)


def read(path) -> Image:
    from pathlib import Path

    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))
