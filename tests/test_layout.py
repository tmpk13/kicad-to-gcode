import pytest

from laser_sweep import layout


def config(**overrides) -> layout.SweepConfig:
    base = dict(
        powers=[200.0, 400.0],
        speeds=[300.0, 600.0],
        spots=[0.1],
        line_length=10.0,
        line_width=1.0,
        labels=False,
    )
    base.update(overrides)
    return layout.SweepConfig(**base)


def test_single_pass_when_the_line_is_no_wider_than_the_spot():
    assert layout.pass_offsets(0.1, 0.1, 0.0) == [0.0]
    assert layout.pass_offsets(0.05, 0.1, 0.0) == [0.0]


def test_pass_centers_span_width_minus_spot():
    offsets = layout.pass_offsets(1.0, 0.1, 0.0)
    assert offsets[0] == pytest.approx(-0.45)
    assert offsets[-1] == pytest.approx(0.45)


def test_passes_never_leave_a_gap():
    for width in (0.3, 1.0, 2.5):
        for spot in (0.05, 0.1, 0.15, 0.2):
            offsets = layout.pass_offsets(width, spot, 0.0)
            for previous, current in zip(offsets, offsets[1:]):
                assert current - previous <= spot + 1e-9


def test_overlap_adds_passes():
    plain = layout.pass_offsets(1.0, 0.1, 0.0)
    overlapped = layout.pass_offsets(1.0, 0.1, 0.5)
    assert len(overlapped) > len(plain)
    assert overlapped[0] == pytest.approx(plain[0])


def test_overlap_outside_the_unit_interval_raises():
    with pytest.raises(ValueError):
        layout.pass_offsets(1.0, 0.1, 1.0)


def test_serpentine_fill_is_one_connected_path():
    paths = layout.fill_line((0.0, 0.0), 10.0, 1.0, 0.1, 0.0, layout.SERPENTINE)
    assert len(paths) == 1
    offsets = layout.pass_offsets(1.0, 0.1, 0.0)
    assert len(paths[0]) == 2 * len(offsets)
    # Alternating direction: every second pass starts on the right.
    assert paths[0][0][0] == pytest.approx(-5.0)
    assert paths[0][2][0] == pytest.approx(5.0)


def test_unidirectional_fill_is_one_path_per_pass():
    paths = layout.fill_line((0.0, 0.0), 10.0, 1.0, 0.1, 0.0, layout.UNIDIRECTIONAL)
    offsets = layout.pass_offsets(1.0, 0.1, 0.0)
    assert len(paths) == len(offsets)
    assert all(path[0][0] < path[1][0] for path in paths)


def test_passes_repeat_the_whole_fill():
    once = layout.fill_line((0.0, 0.0), 10.0, 1.0, 0.1, 0.0, layout.SERPENTINE)
    twice = layout.fill_line(
        (0.0, 0.0), 10.0, 1.0, 0.1, 0.0, layout.SERPENTINE, passes=2
    )
    assert len(twice) == 2 * len(once)
    assert twice[0] == twice[1]


def test_unknown_fill_mode_raises():
    with pytest.raises(ValueError):
        layout.fill_line((0.0, 0.0), 10.0, 1.0, 0.1, 0.0, "spiral")


def test_cell_count_is_the_product_of_the_ranges():
    plan = layout.build(config(spots=[0.05, 0.1, 0.15]))
    assert len(plan.cells) == 2 * 2 * 3
    assert {cell.block for cell in plan.cells} == {0, 1, 2}


def test_power_increases_upwards_and_speed_to_the_right():
    plan = layout.build(config())
    low = next(c for c in plan.cells if c.power == 200 and c.speed == 300)
    high_power = next(c for c in plan.cells if c.power == 400 and c.speed == 300)
    fast = next(c for c in plan.cells if c.power == 200 and c.speed == 600)
    assert high_power.center[1] > low.center[1]
    assert fast.center[0] > low.center[0]


def test_blocks_stack_downwards_by_default():
    plan = layout.build(config(spots=[0.05, 0.1]))
    first = [c for c in plan.cells if c.block == 0]
    second = [c for c in plan.cells if c.block == 1]
    assert min(c.center[1] for c in first) > max(c.center[1] for c in second)


def test_block_columns_place_blocks_side_by_side():
    plan = layout.build(config(spots=[0.05, 0.1], block_cols=2))
    first = [c for c in plan.cells if c.block == 0]
    second = [c for c in plan.cells if c.block == 1]
    assert min(c.center[0] for c in second) > max(c.center[0] for c in first)


def test_center_anchor_puts_the_middle_on_the_origin():
    plan = layout.build(config(anchor="center", origin=(0.0, 0.0)))
    min_x, min_y, max_x, max_y = plan.bounds
    assert (min_x + max_x) / 2 == pytest.approx(0.0)
    assert (min_y + max_y) / 2 == pytest.approx(0.0)


def test_corner_anchor_puts_the_lower_left_on_the_origin():
    plan = layout.build(config(anchor="corner", origin=(5.0, 7.0)))
    min_x, min_y, _, _ = plan.bounds
    assert min_x == pytest.approx(5.0)
    assert min_y == pytest.approx(7.0)


def test_burn_bounds_add_the_beam_radius():
    plan = layout.build(config(spots=[0.2]))
    assert plan.burn_bounds[0] == pytest.approx(plan.bounds[0] - 0.1)
    assert plan.burn_bounds[3] == pytest.approx(plan.bounds[3] + 0.1)


def test_travel_order_alternates_direction_along_rows():
    plan = layout.build(config(speeds=[100.0, 200.0, 300.0]))
    ordered = plan.travel_order()
    assert [cell.col for cell in ordered[:3]] == [0, 1, 2]
    assert [cell.col for cell in ordered[3:6]] == [2, 1, 0]
    assert len(ordered) == len(plan.cells)


def test_labels_widen_the_layout():
    without = layout.build(config(labels=False))
    with_labels = layout.build(config(labels=True))
    assert with_labels.width > without.width
    assert with_labels.height > without.height
    assert with_labels.label_paths


def test_label_power_and_speed_default_to_the_middle_of_the_range():
    plan = layout.build(config(powers=[100.0, 500.0, 900.0], labels=True))
    assert plan.label_power == 500.0


def test_empty_range_is_rejected():
    with pytest.raises(ValueError):
        layout.build(config(powers=[]))
