"""Isolation plans: loop offsets, loop direction, placement and bridge finding."""

from __future__ import annotations

import pytest

from laser_sweep import geom, isolate
from laser_sweep.isolate import IsoConfig

# One machine step on the reference config is 4 microns; a beam narrowed by
# that must not touch copper anywhere.
STEP = 0.004


def square(x: float, y: float, size: float) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def test_one_pass_sits_half_a_spot_off_the_copper():
    assert isolate.offsets_for(IsoConfig(spot=0.2)) == pytest.approx([0.1])


def test_passes_step_out_by_a_spot():
    offsets = isolate.offsets_for(IsoConfig(spot=0.2, passes=3))
    assert offsets == pytest.approx([0.1, 0.3, 0.5])


def test_overlap_tightens_the_step():
    offsets = isolate.offsets_for(IsoConfig(spot=0.2, passes=3, overlap=0.5))
    assert offsets == pytest.approx([0.1, 0.2, 0.3])


def test_margin_pushes_every_loop_off_the_copper():
    offsets = isolate.offsets_for(IsoConfig(spot=0.2, passes=2, margin=0.05))
    assert offsets == pytest.approx([0.15, 0.35])


def test_clear_band_runs_from_half_a_spot_to_half_a_spot():
    offsets = isolate.offsets_for(IsoConfig(spot=0.2, clear=0.6))
    assert offsets[0] == pytest.approx(0.1)
    assert offsets[-1] == pytest.approx(0.5)


def test_a_clear_band_under_one_spot_is_a_single_loop():
    assert isolate.offsets_for(IsoConfig(spot=0.3, clear=0.2)) == pytest.approx([0.15])


@pytest.mark.parametrize(
    "config",
    [
        IsoConfig(spot=0.0),
        IsoConfig(spot=0.1, passes=0),
        IsoConfig(spot=0.1, overlap=1.0),
        IsoConfig(spot=0.1, margin=-1.0),
        IsoConfig(spot=0.1, clear=0.0),
    ],
)
def test_bad_numbers_are_refused(config):
    with pytest.raises(ValueError):
        isolate.offsets_for(config)


def test_a_loop_starts_at_its_lowest_left_corner_and_runs_positive():
    loop = isolate.as_loop([(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)])
    assert loop[0] == (0.0, 0.0)
    assert loop[1][0] > loop[0][0]  # first move is +X
    assert loop[0] == loop[-1]  # and it closes
    rising = [point for point in loop if point[1] > loop[0][1]]
    assert rising  # the first Y move is upwards
    assert isolate.signed_area(loop[:-1]) > 0


def test_a_clockwise_contour_is_turned_around():
    loop = isolate.as_loop([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)])
    assert isolate.signed_area(loop[:-1]) > 0
    assert loop[0] == (0.0, 0.0)
    assert loop[1] == (1.0, 0.0)


def test_corner_anchor_puts_the_whole_job_above_the_origin():
    plan = isolate.build([square(50, 50, 2)], IsoConfig(spot=0.2, passes=2))
    min_x, min_y, _, _ = plan.bounds
    assert min_x == pytest.approx(0.0, abs=1e-6)
    assert min_y == pytest.approx(0.0, abs=1e-6)


def test_center_anchor_centers_the_job():
    plan = isolate.build(
        [square(50, 50, 2)], IsoConfig(spot=0.2, anchor=isolate.ANCHOR_CENTER)
    )
    min_x, min_y, max_x, max_y = plan.bounds
    assert (min_x + max_x) / 2 == pytest.approx(0.0, abs=1e-6)
    assert (min_y + max_y) / 2 == pytest.approx(0.0, abs=1e-6)


def test_keep_anchor_leaves_the_gerber_coordinates_alone():
    plan = isolate.build(
        [square(50, 50, 2)], IsoConfig(spot=0.2, anchor=isolate.ANCHOR_KEEP)
    )
    assert geom.bounds(plan.copper)[0] == pytest.approx(50.0)


def test_mirror_x_flips_the_layer():
    config = IsoConfig(spot=0.2, mirror=isolate.MIRROR_X, anchor=isolate.ANCHOR_KEEP)
    plan = isolate.build([square(10, 0, 2)], config)
    assert geom.bounds(plan.copper)[0] == pytest.approx(-12.0)


def test_the_outline_is_placed_with_the_copper():
    outline = [[(49.0, 49.0), (53.0, 49.0), (53.0, 53.0), (49.0, 49.0)]]
    plan = isolate.build([square(50, 50, 2)], IsoConfig(spot=0.2), outline=outline)
    min_x, min_y, _, _ = plan.bounds
    assert min_x == pytest.approx(0.0, abs=1e-6)
    assert min_y == pytest.approx(0.0, abs=1e-6)


def test_the_beam_never_bites_into_the_copper():
    plan = isolate.build([square(0, 0, 4), square(6, 0, 4)], IsoConfig(spot=0.2, passes=3))
    burn = geom.stroke([loop.points for loop in plan.loops], 0.2 - STEP)
    assert geom.area(geom.intersection(burn, plan.copper)) == pytest.approx(0.0)


def test_the_cleared_band_reaches_its_full_width():
    config = IsoConfig(spot=0.2, clear=0.6)
    plan = isolate.build([square(0, 0, 4)], config)
    band = geom.difference(geom.offset(plan.copper, 0.6 - STEP), plan.copper)
    burn = geom.stroke([loop.points for loop in plan.loops], 0.2)
    left = geom.area(geom.difference(band, burn))
    # Passes meet tangentially, so the chords standing in for their arcs leave
    # hairline slivers at outside corners. They are microns deep, under the
    # machine's step, and a missing pass would leave a third of the band.
    assert left / geom.area(band) < 1e-4


def test_the_burn_parts_two_neighbouring_islands():
    plan = isolate.build([square(0, 0, 4), square(5, 0, 4)], IsoConfig(spot=0.2))
    burn = geom.stroke([loop.points for loop in plan.loops], 0.2)
    board = geom.offset(plan.copper, 1.0)
    assert len(geom.islands(geom.difference(board, burn))) >= 2


def test_copper_closer_than_the_beam_is_reported():
    plan = isolate.build([square(0, 0, 2), square(2.3, 0, 2)], IsoConfig(spot=0.4))
    assert plan.bridged_total == 1
    (bridge,) = plan.bridges
    assert bridge.islands == 2
    assert bridge.clearance == pytest.approx(0.3, abs=0.01)


def test_copper_wider_apart_than_the_beam_is_not_reported():
    plan = isolate.build([square(0, 0, 2), square(2.5, 0, 2)], IsoConfig(spot=0.4))
    assert plan.bridged_total == 0
    assert plan.islands == 2


def test_the_bridge_check_can_be_skipped():
    plan = isolate.build(
        [square(0, 0, 2), square(2.3, 0, 2)], IsoConfig(spot=0.4), check_bridges=False
    )
    assert plan.bridged_total == 0


def test_loops_are_cut_pass_by_pass():
    plan = isolate.build([square(0, 0, 2), square(5, 0, 2)], IsoConfig(spot=0.2, passes=2))
    indexes = [loop.index for loop in plan.loops]
    assert indexes == sorted(indexes)


def test_outside_in_cuts_the_widest_loop_first():
    plan = isolate.build(
        [square(0, 0, 2)], IsoConfig(spot=0.2, passes=2, order=isolate.OUTSIDE_IN)
    )
    assert plan.loops[0].offset > plan.loops[-1].offset


def test_an_unknown_order_is_refused():
    with pytest.raises(ValueError, match="order"):
        isolate.build([square(0, 0, 2)], IsoConfig(spot=0.2, order="sideways"))
