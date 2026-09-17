"""Backlash as a sweep axis: one block per assumed value, swapped safely."""

from __future__ import annotations

import pytest

from backlash_sim import geometry_points, simulate
from laser_sweep import gcode, layout


def config(**overrides) -> layout.SweepConfig:
    base = dict(
        powers=[500.0],
        speeds=[400.0],
        spots=[0.1],
        line_length=3.0,
        line_width=0.3,
        invert=True,
        border=0.4,
        labels=False,
    )
    base.update(overrides)
    return layout.SweepConfig(**base)


def build(backlashes, **overrides):
    plan = layout.build(config(backlashes=backlashes, **overrides))
    job = gcode.generate(plan, gcode.GcodeOptions(approach=1.0))
    return plan, job


def test_each_value_gets_its_own_block():
    assert layout.blocks(config(backlashes=[0.0, 0.2])) == [(0.1, 0.0), (0.1, 0.2)]


def test_spot_and_backlash_multiply_out_spot_first():
    pairs = layout.blocks(config(spots=[0.1, 0.2], backlashes=[0.0, 0.3]))
    assert pairs == [(0.1, 0.0), (0.1, 0.3), (0.2, 0.0), (0.2, 0.3)]


def test_without_a_sweep_there_is_one_block_per_spot():
    assert layout.blocks(config(spots=[0.1, 0.2])) == [(0.1, None), (0.2, None)]


def test_cells_carry_the_value_of_their_block():
    plan = layout.build(config(backlashes=[0.0, 0.2]))
    assert {cell.block: cell.backlash for cell in plan.cells} == {0: 0.0, 1: 0.2}


def test_a_cell_keeps_its_geometry_whatever_the_value():
    plain = layout.build(config(backlashes=[0.0]))
    swept = layout.build(config(backlashes=[0.2]))
    assert plain.cells[0].paths == swept.cells[0].paths


def test_the_block_label_names_the_value():
    plan = layout.build(config(backlashes=[0.15], labels=True))
    assert plan.label_paths  # d0.1 b0.15 is burned above the block
    text = layout._block_label(0.1, 0.15, config(backlashes=[0.15]))
    assert text == "d0.1 b0.15"


def test_the_header_lists_the_swept_values():
    _, job = build([0.0, 0.1, 0.2])
    assert "Backlash:     swept 0, 0.1, 0.2 mm" in job.text
    assert "b=backlash" in job.text


def test_each_cell_names_its_value_in_the_file():
    _, job = build([0.0, 0.2])
    assert "backlash=0 block=0" in job.text
    assert "backlash=0.2 block=1" in job.text


def test_the_records_carry_the_value():
    _, job = build([0.0, 0.2])
    assert sorted(record["backlash"] for record in job.records) == [0.0, 0.2]


def test_an_uncompensated_block_is_not_split_at_reversals():
    """A value of 0 is the control: the loops run as one path each."""
    plan, job = build([0.0])
    block = job.text.split("; cell")[1]
    assert block.count("M4 S0") == len(plan.cells[0].paths)


def test_a_compensated_block_is_split_at_reversals():
    plan, job = build([0.2])
    block = job.text.split("; cell")[1]
    assert block.count("M4 S0") > len(plan.cells[0].paths)


def test_the_value_is_swapped_with_both_axes_positive():
    """Commanded and head positions only agree there, so nothing is unwound."""
    _, job = build([0.0, 0.2])
    assert "; both axes into the positive state before 0.2 mm" in job.text


def cut_ends(text: str, real: float):
    steps = simulate(text, (real, real))
    return [tuple(head) for kind, _, _, _, head in steps if kind == "G1"]


def block_range(plan, block: int) -> tuple[int, int]:
    """Which cut moves belong to one block, in the order the job burns them.

    Blocks are cut in order and a compensated cut is split with rapids, not
    with extra cut moves, so every geometry point is still one G1.
    """
    counts = [
        (cell.block, sum(len(path) - 1 for path in cell.paths))
        for cell in plan.travel_order()
    ]
    start = sum(count for owner, count in counts if owner < block)
    size = sum(count for owner, count in counts if owner == block)
    return start, start + size


@pytest.mark.parametrize("real", [0.1, 0.25])
def test_the_block_that_assumes_the_real_dead_band_lands_on_the_geometry(real):
    plan, job = build([0.0, real])
    wanted = geometry_points(plan)
    ends = cut_ends(job.text, real)
    assert len(ends) == len(wanted)
    low, high = block_range(plan, 1)
    for got, want in zip(ends[low:high], wanted[low:high]):
        assert got == pytest.approx(want, abs=2e-3)


def test_the_uncompensated_block_misses_by_the_dead_band():
    real = 0.25
    plan, job = build([0.0, real])
    wanted = geometry_points(plan)
    ends = cut_ends(job.text, real)
    low, high = block_range(plan, 0)
    worst = max(
        max(abs(got[axis] - want[axis]) for axis in (0, 1))
        for got, want in zip(ends[low:high], wanted[low:high])
    )
    assert worst == pytest.approx(real, abs=2e-3)


def test_the_compensated_block_never_burns_while_the_head_stands_still():
    real = 0.2
    plan, job = build([0.0, real])
    steps = simulate(job.text, (real, real))
    cuts = [step for step in steps if step[0] == "G1"]
    low, high = block_range(plan, 1)
    for _, motor0, motor1, head0, head1 in cuts[low:high]:
        for axis in (0, 1):
            assert abs(head1[axis] - head0[axis]) == pytest.approx(
                abs(motor1[axis] - motor0[axis]), abs=1e-9
            )
