"""Excellon drill reader: hole centers and diameters, in millimetres.

A laser will not drill FR4, so the holes are only ever burned as marks for a
drill to follow. That is all this needs from the file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .gerber import MM_PER_INCH, GerberError, Point

_TOOL = re.compile(r"^T(\d+)(?:.*?C([\d.]+))?", re.IGNORECASE)
_COORD = re.compile(r"([XY])([-+]?[\d.]+)")


@dataclass(frozen=True)
class Hole:
    at: Point
    diameter: float
    end: Point | None = None  # a slot runs from `at` to `end`

    @property
    def slot(self) -> bool:
        return self.end is not None


@dataclass
class Drills:
    holes: list[Hole]
    tools: dict[int, float]

    @property
    def diameters(self) -> list[float]:
        return sorted({hole.diameter for hole in self.holes})


def parse(text: str) -> Drills:
    """Read an Excellon file. Metric or inch, decimal or zero suppressed."""
    tools: dict[int, float] = {}
    holes: list[Hole] = []
    scale = 1.0
    decimals = 3
    suppress = "leading"
    header = False
    tool = 0
    position: Point = (0.0, 0.0)

    def value(text: str) -> float:
        if "." in text:
            return float(text) * scale
        sign = -1.0 if text.startswith("-") else 1.0
        digits = text.lstrip("+-")
        if suppress == "trailing":
            digits = digits.ljust(decimals + 3, "0")
        return sign * int(digits) / (10.0**decimals) * scale

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("%") or line == "M95":
            # The header ends here; everything after it is hole coordinates.
            header = False
            continue
        if line == "M48":
            header = True
            continue
        if line.startswith("METRIC"):
            scale, decimals = 1.0, 3
            if "TZ" in line:
                suppress = "leading"
            elif "LZ" in line:
                suppress = "trailing"
            continue
        if line.startswith("INCH"):
            scale, decimals = MM_PER_INCH, 4
            if "TZ" in line:
                suppress = "leading"
            elif "LZ" in line:
                suppress = "trailing"
            continue
        if line.startswith(("M30", "M00")):
            break
        if line.startswith("T"):
            match = _TOOL.match(line)
            if not match:
                continue
            number = int(match.group(1))
            if match.group(2) is not None:
                tools[number] = float(match.group(2)) * scale
                header = True
            elif not header:
                tool = number
            else:
                tools.setdefault(number, 0.0)
            continue
        if header or line.startswith(("FMAT", "G90", "G05", "G93", "VER", "ICI")):
            continue
        found = dict(_COORD.findall(line))
        if not found:
            continue
        point = (
            value(found["X"]) if "X" in found else position[0],
            value(found["Y"]) if "Y" in found else position[1],
        )
        diameter = tools.get(tool, 0.0)
        if "G85" in line.upper():
            # A slot: the second pair of coordinates is the far end.
            pairs = _COORD.findall(line)
            if len(pairs) >= 4:
                start = (value(pairs[0][1]), value(pairs[1][1]))
                end = (value(pairs[2][1]), value(pairs[3][1]))
                holes.append(Hole(start, diameter, end))
                position = end
                continue
        holes.append(Hole(point, diameter))
        position = point

    if not tools and holes:
        raise GerberError("drill file defines no tools")
    return Drills(holes=holes, tools=tools)


def read(path) -> Drills:
    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))


CROSS, CIRCLE, DOT = "cross", "circle", "dot"


def marks(drills: Drills, style: str, spot: float) -> list[list[Point]]:
    """Paths that mark where to drill, without trying to cut the hole."""
    paths: list[list[Point]] = []
    for hole in drills.holes:
        centers = [hole.at] if not hole.slot else [hole.at, hole.end]
        for center in centers:
            paths.extend(_mark(center, hole.diameter, style, spot))
    return paths


def _mark(center: Point, diameter: float, style: str, spot: float) -> list[list[Point]]:
    x, y = center
    if style == CROSS:
        arm = max(spot, min(diameter, 0.8)) / 2.0
        return [
            [(x - arm, y), (x + arm, y)],
            [(x, y - arm), (x, y + arm)],
        ]
    if style == DOT:
        arm = spot / 2.0
        return [[(x - arm, y), (x + arm, y)]]
    if style == CIRCLE:
        # The ring's outer edge lands on the hole wall, so the burn stays
        # inside the material the drill will remove.
        radius = max(spot, diameter - spot) / 2.0
        count = max(12, int(math.ceil(math.pi * 2.0 * radius / 0.1)))
        points = [
            (
                x + radius * math.cos(2.0 * math.pi * i / count),
                y + radius * math.sin(2.0 * math.pi * i / count),
            )
            for i in range(count)
        ]
        return [points + [points[0]]]
    raise ValueError(f"unknown drill mark style {style!r}")
