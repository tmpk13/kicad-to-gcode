"""Apply backlash compensation and approach moves to an existing gcode file."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from .backlash import Compensator, Point, format_coord, parse_backlash

# A parenthesised comment, or a word: one letter and a number.
WORD = re.compile(r"\([^)]*\)|([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")

REFUSED_G = {
    2: "arcs, export them as line segments",
    3: "arcs, export them as line segments",
    10: "coordinate system changes",
    20: "inches",
    28: "moves to a predefined position",
    30: "moves to a predefined position",
    38: "probing",
    53: "machine coordinate moves",
    91: "relative moves",
    92: "coordinate offsets",
}


class PostprocessError(ValueError):
    """The file contains something the compensation cannot follow."""


@dataclass
class Line:
    text: str
    code: str
    tail: str
    words: list[tuple[str, float, str]]

    def has(self, letter: str) -> bool:
        return any(word[0] == letter for word in self.words)

    def value(self, letter: str) -> float | None:
        for word in self.words:
            if word[0] == letter:
                return word[1]
        return None

    @property
    def motion(self) -> str | None:
        for letter, value, _ in self.words:
            if letter == "G" and value in (0.0, 1.0):
                return f"G{int(value)}"
        return None

    @property
    def xy(self) -> bool:
        return self.has("X") or self.has("Y")


def parse_line(text: str) -> Line:
    code, sep, tail = text.partition(";")
    words = []
    for match in WORD.finditer(code):
        if match.group(1) is not None:
            words.append(
                (match.group(1).upper(), float(match.group(2)), match.group(2))
            )
    return Line(text, code, sep + tail, words)


@dataclass
class Stats:
    moves: int = 0
    staged: int = 0
    take_ups: int = 0
    splits: int = 0

    def summary(self) -> str:
        return (
            f"moves {self.moves}, staging rapids {self.staged},"
            f" take-ups {self.take_ups}, cuts split {self.splits}"
        )


def compensate(
    text: str,
    backlash: Point,
    approach: float = 0.0,
    decimals: int = 3,
    source: str = "",
) -> tuple[str, Stats]:
    """Rewrite absolute G0/G1 moves so the head lands where the file says.

    Lines without X or Y pass through untouched. A reversal inside a cut is
    crossed with the beam off: the cut is closed, a rapid moves the motor
    through the dead band, and the cut reopens with the file's own modal S
    and F restored. With `approach`, every path start is entered from a
    staging point below left of it.
    """
    lines = [parse_line(raw) for raw in text.splitlines()]
    resolved = _resolve_motion(lines)
    xy_lines = sorted(resolved)
    if not xy_lines:
        raise PostprocessError("the file has no X or Y moves")
    following = {
        index: resolved[after]
        for index, after in zip(xy_lines, xy_lines[1:])
    }

    def coord(value: float) -> str:
        return format_coord(value, decimals)

    state = Compensator(backlash)
    stats = Stats()
    spindle: str | None = None
    s_text: str | None = None
    f_text: str | None = None
    previous_cut = False
    out: list[str] = []

    for index, line in enumerate(lines):
        for letter, value, value_text in line.words:
            if letter == "G" and int(value) in REFUSED_G:
                raise PostprocessError(
                    f"line {index + 1}: G{int(value)} is not supported"
                    f" ({REFUSED_G[int(value)]})"
                )
            if letter == "M" and int(value) in (3, 4):
                spindle = f"M{int(value)}"
            elif letter == "S":
                s_text = value_text
            elif letter == "F":
                f_text = value_text
        if index not in resolved:
            out.append(line.text)
            continue

        motion = resolved[index]
        if state.position is None:
            if motion != "G0" or not (line.has("X") and line.has("Y")):
                raise PostprocessError(
                    f"line {index + 1}: the first move must be a G0 giving"
                    " both X and Y"
                )
            target = (line.value("X"), line.value("Y"))
        else:
            x = line.value("X")
            y = line.value("Y")
            target = (
                state.position[0] if x is None else x,
                state.position[1] if y is None else y,
            )

        restore = False
        if motion == "G0":
            starts_path = following.get(index) == "G1"
            if state.position is None:
                # Any positive move longer than the dead band ends in the
                # positive state, wherever the machine was before.
                distance = approach if approach > 0 else 2.0 * max(backlash)
                out.append(_banner(backlash, approach, source))
                out.append("; both axes into the positive state")
            elif approach > 0 and starts_path:
                distance = approach
            else:
                distance = 0.0
            if distance > 0:
                sx, sy = state.move((target[0] - distance, target[1] - distance))
                out.append(f"G0 X{coord(sx)} Y{coord(sy)}")
                stats.staged += 1
        else:
            take = state.take_up(target)
            if take is not None:
                if previous_cut:
                    out.append("G1F1")
                    out.append(f"{spindle} S0" if spindle else "S0")
                    out.append(f"G0 X{coord(take[0])} Y{coord(take[1])}")
                    out.append("G1F1")
                    stats.splits += 1
                    restore = True
                else:
                    out.append(f"G0 X{coord(take[0])} Y{coord(take[1])}")
                    stats.take_ups += 1

        commanded = state.move(target)
        code = _rewrite(line, commanded, motion, decimals)
        if restore:
            code = _restore(code, line, s_text, f_text)
        out.append(code + line.tail)
        stats.moves += 1
        previous_cut = motion == "G1"

    return "\n".join(out) + "\n", stats


def _resolve_motion(lines: list[Line]) -> dict[int, str]:
    """Motion mode of every line that moves X or Y, honoring modal G0/G1."""
    resolved: dict[int, str] = {}
    modal: str | None = None
    for index, line in enumerate(lines):
        if line.motion is not None:
            modal = line.motion
        if line.xy:
            if modal is None:
                raise PostprocessError(
                    f"line {index + 1}: axis words before any G0 or G1"
                )
            resolved[index] = modal
    return resolved


def _rewrite(line: Line, commanded: Point, motion: str, decimals: int) -> str:
    """The code part of the line with X and Y replaced and the motion explicit.

    A rapid inserted in front of a modal line would otherwise turn that cut
    into a rapid.
    """

    def substitute(match: re.Match) -> str:
        letter = match.group(1)
        if letter is None:
            return match.group(0)
        if letter.upper() == "X":
            return letter + format_coord(commanded[0], decimals)
        if letter.upper() == "Y":
            return letter + format_coord(commanded[1], decimals)
        return match.group(0)

    code = WORD.sub(substitute, line.code)
    if line.motion is None:
        code = f"{motion} {code.strip()}"
    return code


def _restore(code: str, line: Line, s_text: str | None, f_text: str | None) -> str:
    extra = []
    if s_text is not None and not line.has("S"):
        extra.append(f"S{s_text}")
    if f_text is not None and not line.has("F"):
        extra.append(f"F{f_text}")
    if not extra:
        return code
    return code.rstrip() + " " + " ".join(extra)


def _banner(backlash: Point, approach: float, source: str) -> str:
    parts = [f"backlash X {backlash[0]:g} mm, Y {backlash[1]:g} mm"]
    if approach > 0:
        parts.append(f"approach {approach:g} mm")
    if source:
        parts.append(f"from {source}")
    return "; laser-backlash: " + ", ".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laser-backlash",
        description="Rewrite an existing gcode file so a machine with backlash"
        " puts the head where the file says: coordinates after a reversal are"
        " shifted, reversals inside cuts are crossed with the beam off, and"
        " path starts can be entered from a fixed direction.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="absolute mm gcode, G0/G1 only")
    parser.add_argument(
        "--backlash",
        default="0",
        metavar="X[,Y]",
        help="mm of lost motion on a direction reversal, one value or X,Y",
    )
    parser.add_argument(
        "--approach",
        type=float,
        default=0.0,
        help="mm: enter every path start from a point this far below and left",
    )
    parser.add_argument("--decimals", type=int, default=3, help="coordinate decimals")
    parser.add_argument(
        "-o", "--output", type=Path, help="default: <name>.comp.gcode next to the input"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        backlash = parse_backlash(args.backlash)
    except ValueError as exc:
        parser.error(str(exc))
    if args.approach < 0:
        parser.error("approach must be >= 0")
    if not any(backlash) and args.approach <= 0:
        parser.error("nothing to do: give --backlash and/or --approach")

    output = args.output or args.input.with_name(
        args.input.stem + ".comp" + args.input.suffix
    )
    if output.resolve() == args.input.resolve():
        parser.error("the output would overwrite the input")

    try:
        text, stats = compensate(
            args.input.read_text(),
            backlash,
            approach=args.approach,
            decimals=args.decimals,
            source=args.input.name,
        )
    except PostprocessError as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(stats.summary())
    print(f"wrote      {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
