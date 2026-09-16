"""Minimal seven-segment stroke font used to burn numeric labels."""

from __future__ import annotations

Point = tuple[float, float]
Polyline = list[Point]

# Glyphs live in a unit cell: 0..GLYPH_W across, 0..1 tall, origin bottom left.
GLYPH_W = 0.6
GLYPH_GAP = 0.2

_SEGMENTS: dict[str, Polyline] = {
    "a": [(0.0, 1.0), (GLYPH_W, 1.0)],
    "b": [(GLYPH_W, 1.0), (GLYPH_W, 0.5)],
    "c": [(GLYPH_W, 0.5), (GLYPH_W, 0.0)],
    "d": [(0.0, 0.0), (GLYPH_W, 0.0)],
    "e": [(0.0, 0.5), (0.0, 0.0)],
    "f": [(0.0, 1.0), (0.0, 0.5)],
    "g": [(0.0, 0.5), (GLYPH_W, 0.5)],
}

_SEVEN_SEG = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
    # Letters a seven segment cell can render without becoming a digit.
    "A": "abcefg",
    "C": "adef",
    "E": "adefg",
    "F": "aefg",
    "H": "bcefg",
    "I": "ef",
    "J": "bcde",
    "L": "def",
    "P": "abefg",
    "U": "bcdef",
    "b": "cdefg",
    "c": "deg",
    "d": "bcdeg",
    "h": "cefg",
    "n": "ceg",
    "o": "cdeg",
    "r": "eg",
    "t": "defg",
    "u": "cde",
}

# Characters outside the seven-segment grid, plus the advance they consume.
_EXTRA: dict[str, tuple[list[Polyline], float]] = {
    ".": ([[(0.0, 0.0), (0.15, 0.0)]], 0.15),
    "-": ([[(0.0, 0.5), (GLYPH_W, 0.5)]], GLYPH_W),
    "/": ([[(0.0, 0.0), (GLYPH_W, 1.0)]], GLYPH_W),
    " ": ([], GLYPH_W * 0.6),
}


def _same(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def chain(segments: list[Polyline]) -> list[Polyline]:
    """Join segments that share an endpoint, so a glyph burns in few passes."""
    remaining = [list(segment) for segment in segments]
    chains: list[Polyline] = []
    while remaining:
        current = remaining.pop(0)
        extended = True
        while extended:
            extended = False
            for index, (start, end) in enumerate(remaining):
                if _same(current[-1], start):
                    current.append(end)
                elif _same(current[-1], end):
                    current.append(start)
                elif _same(current[0], start):
                    current.insert(0, end)
                elif _same(current[0], end):
                    current.insert(0, start)
                else:
                    continue
                remaining.pop(index)
                extended = True
                break
        chains.append(current)
    return chains


def glyph(char: str) -> tuple[list[Polyline], float]:
    """Return the strokes of one character and the advance width it uses."""
    if char in _SEVEN_SEG:
        return chain([_SEGMENTS[s] for s in _SEVEN_SEG[char]]), GLYPH_W
    if char in _EXTRA:
        strokes, advance = _EXTRA[char]
        return [list(stroke) for stroke in strokes], advance
    raise KeyError(f"no glyph for {char!r}")


def text_width(text: str, height: float) -> float:
    """Width of `text` when rendered at `height` millimetres."""
    total = 0.0
    for index, char in enumerate(text):
        _, advance = glyph(char)
        total += advance
        if index < len(text) - 1:
            total += GLYPH_GAP
    return total * height


def text_paths(
    text: str,
    x: float,
    y: float,
    height: float,
    anchor: str = "lb",
) -> list[Polyline]:
    """Render `text` as polylines, positioned by a two letter anchor.

    The anchor is horizontal ("l", "c", "r") followed by vertical
    ("b", "c", "t"), describing which point of the text box lands on (x, y).
    """
    if len(anchor) != 2 or anchor[0] not in "lcr" or anchor[1] not in "bct":
        raise ValueError(f"bad anchor {anchor!r}")

    width = text_width(text, height)
    if anchor[0] == "c":
        x -= width / 2.0
    elif anchor[0] == "r":
        x -= width
    if anchor[1] == "c":
        y -= height / 2.0
    elif anchor[1] == "t":
        y -= height

    paths: list[Polyline] = []
    cursor = x
    for char in text:
        strokes, advance = glyph(char)
        for stroke in strokes:
            paths.append([(cursor + px * height, y + py * height) for px, py in stroke])
        cursor += (advance + GLYPH_GAP) * height
    return paths


def format_number(value: float, decimals: int = 3) -> str:
    """Compact label text: no exponent, no trailing zeros, no leading '+'."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"
