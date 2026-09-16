"""A head with a dead band, driven through gcode, for the tests."""

from __future__ import annotations

import re

import pytest

AXIS = re.compile(r"([XYxy])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
MOTION = re.compile(r"\bG0*([01])(?!\d)", re.IGNORECASE)


def moves(text: str) -> list[tuple[str, float, float]]:
    """(kind, x, y) for every line that moves X or Y; missing words repeat."""
    result = []
    modal = None
    x = y = 0.0
    for raw in text.splitlines():
        code = raw.partition(";")[0]
        motion = MOTION.search(code)
        if motion:
            modal = f"G{motion.group(1)}"
        words = {letter.upper(): float(value) for letter, value in AXIS.findall(code)}
        if not words:
            continue
        assert modal is not None, raw
        x = words.get("X", x)
        y = words.get("Y", y)
        result.append((modal, x, y))
    return result


def xy(line: str) -> tuple[float, float]:
    words = {letter.upper(): float(value) for letter, value in AXIS.findall(line)}
    return words["X"], words["Y"]


def cut_targets(text: str) -> list[tuple[float, float]]:
    return [(x, y) for kind, x, y in moves(text) if kind == "G1"]


def geometry_points(plan_) -> list[tuple[float, float]]:
    """Every point a cut move should end on, in the order the job burns them."""
    points = [
        tuple(point)
        for cell in plan_.travel_order()
        for path in cell.paths
        for point in path[1:]
    ]
    points += [tuple(point) for path in plan_.label_paths for point in path[1:]]
    return points


def simulate(text: str, backlash: tuple[float, float], start_negative: bool = True):
    """Drive a head with a dead band through the moves of `text`.

    The motor drags the head, which only moves once the motor has crossed the
    backlash in the new direction. Starting in the negative state proves the
    job establishes its own reference before cutting.
    """
    motor = [0.0, 0.0]
    head = [backlash[0] if start_negative else 0.0, backlash[1] if start_negative else 0.0]
    steps = []
    for kind, x, y in moves(text):
        motor_before, head_before = list(motor), list(head)
        for axis, value in ((0, x), (1, y)):
            motor[axis] = value
            if motor[axis] > head[axis]:
                head[axis] = motor[axis]
            elif motor[axis] + backlash[axis] < head[axis]:
                head[axis] = motor[axis] + backlash[axis]
        steps.append((kind, motor_before, list(motor), head_before, list(head)))
    return steps


def assert_cuts_land(text: str, wanted, backlash, tol: float = 2e-3) -> None:
    """Every cut ends on its point, and the head never stands still under power."""
    steps = simulate(text, backlash)
    ends = [tuple(head) for kind, _, _, _, head in steps if kind == "G1"]
    assert len(ends) == len(wanted)
    for got, want in zip(ends, wanted):
        assert got == pytest.approx(want, abs=tol)
    for kind, m0, m1, h0, h1 in steps:
        if kind == "G1":
            for axis in (0, 1):
                assert abs(h1[axis] - h0[axis]) == pytest.approx(
                    abs(m1[axis] - m0[axis]), abs=1e-9
                ), "beam on while the head stood still"
