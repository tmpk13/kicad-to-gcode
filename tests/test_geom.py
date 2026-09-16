"""Polygon sets: union, offset, islands and building copper out of figures."""

from __future__ import annotations

import math

import pytest

from laser_sweep import geom, gerber


def square(x: float, y: float, size: float) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def test_union_merges_overlapping_squares():
    result = geom.union([square(0, 0, 1), square(0.5, 0, 1)])
    assert len(result) == 1
    assert geom.area(result) == pytest.approx(1.5, abs=1e-6)


def test_union_keeps_separate_squares_apart():
    result = geom.union([square(0, 0, 1), square(3, 0, 1)])
    assert len(result) == 2


def test_difference_cuts_a_hole():
    result = geom.difference([square(0, 0, 3)], [square(1, 1, 1)])
    assert geom.area(result) == pytest.approx(8.0, abs=1e-6)
    assert len(result) == 2


def test_offset_grows_by_the_beam_radius():
    grown = geom.offset([square(0, 0, 2)], 0.5)
    # A square grows by its sides plus a quarter circle at each corner.
    expected = 4.0 + 4 * 2 * 0.5 + math.pi * 0.25
    # The corners are arcs drawn as chords, so the polygon falls just inside.
    assert geom.area(grown) == pytest.approx(expected, abs=1e-2)


def test_negative_offset_shrinks():
    assert geom.area(geom.offset([square(0, 0, 2)], -0.5)) == pytest.approx(1.0, abs=1e-3)


def test_stroke_covers_the_pen_path():
    covered = geom.stroke([[(0.0, 0.0), (2.0, 0.0)]], 0.4)
    expected = 2.0 * 0.4 + math.pi * 0.2**2
    assert geom.area(covered) == pytest.approx(expected, abs=1e-2)


def test_sweep_drags_a_rectangle_along_a_segment():
    outline = gerber.rect_points(0.4, 0.2)
    covered = geom.sweep([(0.0, 0.0), (1.0, 0.0)], outline)
    assert geom.area(covered) == pytest.approx(1.0 * 0.2 + 0.4 * 0.2, abs=1e-6)


def test_islands_splits_separate_copper():
    assert len(geom.islands(geom.union([square(0, 0, 1), square(3, 0, 1)]))) == 2


def test_islands_keeps_a_hole_with_its_outline():
    ring = geom.difference([square(0, 0, 3)], [square(1, 1, 1)])
    (piece,) = geom.islands(ring)
    assert len(piece) == 2


def test_islands_counts_copper_inside_a_hole_separately():
    ring = geom.difference([square(0, 0, 5)], [square(1, 1, 3)])
    pieces = geom.islands(ring + [square(2, 2, 1)])
    assert len(pieces) == 2


def test_touching_corners_count_as_one_island():
    pieces = geom.islands(geom.union([square(0, 0, 1), square(1, 1, 1)]))
    assert len(pieces) == 1


def test_contains_respects_holes():
    (piece,) = geom.islands(geom.difference([square(0, 0, 3)], [square(1, 1, 1)]))
    assert geom.contains(piece, (0.5, 0.5))
    assert not geom.contains(piece, (1.5, 1.5))
    assert not geom.contains(piece, (5.0, 5.0))


def test_copper_applies_clear_polarity_in_order():
    image = gerber.Image(
        figures=[
            gerber.Fill(((True, tuple(square(0, 0, 3))),), dark=True),
            gerber.Fill(((True, tuple(square(1, 1, 1))),), dark=False),
        ]
    )
    assert geom.area(geom.copper(image)) == pytest.approx(8.0, abs=1e-6)


def test_copper_unions_a_stroke_with_a_pad():
    image = gerber.Image(
        figures=[
            gerber.Draw(((0.0, 0.0), (2.0, 0.0)), width=0.4),
            gerber.Fill(((True, tuple(square(1.9, -0.5, 1.0))),)),
        ]
    )
    copper = geom.copper(image)
    assert len(copper) == 1


def test_copper_resolves_an_aperture_hole():
    image = gerber.Image(
        figures=[
            gerber.Fill(
                (
                    (True, gerber.circle_points((0.0, 0.0), 1.0)),
                    (False, gerber.circle_points((0.0, 0.0), 0.5)),
                )
            )
        ]
    )
    area = geom.area(geom.copper(image))
    assert area == pytest.approx(math.pi * (1.0**2 - 0.5**2), abs=1e-2)


def test_bounds_of_nothing_is_the_origin():
    assert geom.bounds([]) == (0.0, 0.0, 0.0, 0.0)
