"""Reading drill files and turning holes into marks."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from laser_sweep import excellon

DATA = Path(__file__).parent / "data"

METRIC = """M48
FMAT,2
METRIC
T1C0.800
T2C1.000
%
G90
G05
T1
X10.0Y-20.0
X12.5Y-20.0
T2
X30.0Y-5.0
T0
M30
"""


def test_tools_and_holes_are_read():
    drills = excellon.parse(METRIC)
    assert drills.tools == {1: 0.8, 2: 1.0}
    assert len(drills.holes) == 3
    assert drills.holes[0].at == (10.0, -20.0)
    assert drills.holes[-1].diameter == 1.0


def test_diameters_are_listed_once_each():
    assert excellon.parse(METRIC).diameters == [0.8, 1.0]


def test_inch_files_convert_to_mm():
    text = METRIC.replace("METRIC", "INCH").replace("X10.0Y-20.0", "X1.0Y0.0")
    drills = excellon.parse(text)
    assert drills.holes[0].at[0] == pytest.approx(25.4)


def test_zero_suppressed_coordinates():
    text = "M48\nMETRIC,TZ\nT1C0.800\n%\nG90\nT1\nX010000Y-020000\nM30\n"
    drills = excellon.parse(text)
    assert drills.holes[0].at == pytest.approx((10.0, -20.0))


def test_coordinates_are_modal():
    text = "M48\nMETRIC\nT1C0.800\n%\nG90\nT1\nX10.0Y-20.0\nX12.0\nM30\n"
    drills = excellon.parse(text)
    assert drills.holes[1].at == (12.0, -20.0)


def test_a_slot_keeps_both_ends():
    text = "M48\nMETRIC\nT1C0.800\n%\nG90\nT1\nX10.0Y-20.0G85X14.0Y-20.0\nM30\n"
    (hole,) = excellon.parse(text).holes
    assert hole.slot
    assert hole.end == (14.0, -20.0)


def test_cross_marks_are_two_strokes_through_the_center():
    drills = excellon.parse(METRIC)
    marks = excellon.marks(drills, excellon.CROSS, 0.1)
    assert len(marks) == 6
    horizontal, vertical = marks[0], marks[1]
    assert horizontal[0][1] == horizontal[1][1] == -20.0
    assert vertical[0][0] == vertical[1][0] == 10.0


def test_circle_marks_stay_inside_the_hole():
    drills = excellon.parse(METRIC)
    (mark,) = excellon.marks(drills, excellon.CIRCLE, 0.1)[:1]
    radii = [math.dist(point, (10.0, -20.0)) for point in mark]
    assert max(radii) == pytest.approx((0.8 - 0.1) / 2.0, abs=1e-6)
    assert mark[0] == mark[-1]


def test_a_slot_is_marked_at_both_ends():
    text = "M48\nMETRIC\nT1C0.800\n%\nG90\nT1\nX10.0Y-20.0G85X14.0Y-20.0\nM30\n"
    marks = excellon.marks(excellon.parse(text), excellon.CROSS, 0.1)
    assert len(marks) == 4


def test_an_unknown_mark_style_is_refused():
    with pytest.raises(ValueError, match="mark style"):
        excellon.marks(excellon.parse(METRIC), "spiral", 0.1)


def test_the_fixture_drill_file_reads():
    drills = excellon.read(DATA / "board-PTH.drl")
    assert len(drills.holes) == 3
    assert drills.diameters == [0.8, 1.0]
