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


def test_frame_starts_a_beam_radius_outside_the_island():
    offsets = layout.frame_offsets(0.5, 0.1, 0.0)
    assert offsets[0] == pytest.approx(0.05)


def test_frame_reaches_the_outer_edge_of_the_border():
    for spot in (0.05, 0.1, 0.15, 0.2):
        offsets = layout.frame_offsets(0.5, spot, 0.0)
        assert offsets[-1] + spot / 2 == pytest.approx(0.5)


def test_frame_loops_never_leave_a_gap():
    for border in (0.3, 0.5, 1.0):
        for spot in (0.05, 0.1, 0.15):
            offsets = layout.frame_offsets(border, spot, 0.0)
            for previous, current in zip(offsets, offsets[1:]):
                assert current - previous <= spot + 1e-9


def test_a_border_no_wider_than_the_spot_gives_one_loop_that_spares_the_island():
    assert layout.frame_offsets(0.1, 0.1, 0.0) == [0.05]
    # Too narrow to cut: the frame widens rather than the island shrinking.
    assert layout.frame_offsets(0.05, 0.2, 0.0) == [0.1]


@pytest.mark.parametrize(("border", "spot"), [(0.5, 0.0), (0.0, 0.1), (-1.0, 0.1)])
def test_frame_offsets_reject_impossible_geometry(border, spot):
    with pytest.raises(ValueError):
        layout.frame_offsets(border, spot, 0.0)


def test_frame_loops_are_closed_rectangles_around_the_island():
    loops = layout.frame_loops((0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5)
    assert len(loops) == len(layout.frame_offsets(0.5, 0.1, 0.0))
    for loop in loops:
        assert len(loop) == 5
        assert loop[0] == loop[-1]
    inner = loops[0]
    assert min(abs(y) for _, y in inner) == pytest.approx(0.25 + 0.05)
    assert min(abs(x) for x, _ in inner) == pytest.approx(4.0 + 0.05)


def test_frame_loop_order_is_settable():
    inside_out = layout.frame_loops(
        (0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5, order=layout.INSIDE_OUT
    )
    outside_in = layout.frame_loops(
        (0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5, order=layout.OUTSIDE_IN
    )
    assert inside_out == list(reversed(outside_in))
    assert _height(inside_out[0]) < _height(inside_out[-1])
    assert _height(outside_in[0]) > _height(outside_in[-1])


def test_frame_passes_repeat_every_loop():
    once = layout.frame_loops((0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5)
    twice = layout.frame_loops((0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5, passes=2)
    assert len(twice) == 2 * len(once)


def test_unknown_frame_order_raises():
    with pytest.raises(ValueError):
        layout.frame_loops((0.0, 0.0), 8.0, 0.5, 0.1, 0.0, 0.5, order="spiral")


def _height(loop) -> float:
    ys = [y for _, y in loop]
    return max(ys) - min(ys)


def test_inverted_cells_take_up_the_island_plus_two_borders():
    assert layout.cell_size(config(), 0.1) == (10.0, 1.0)
    assert layout.cell_size(config(invert=True, border=0.5), 0.1) == (11.0, 2.0)


def test_inverted_cells_do_not_collide():
    plan = layout.build(config(invert=True, border=0.5, line_width=0.5))
    columns = sorted({cell.center[0] for cell in plan.cells})
    rows = sorted({cell.center[1] for cell in plan.cells})
    assert columns[1] - columns[0] == pytest.approx(10.0 + 1.0 + 3.0)
    assert rows[1] - rows[0] == pytest.approx(0.5 + 1.0 + 2.0)


def test_no_inverted_path_enters_the_island():
    plan = layout.build(
        config(invert=True, border=0.5, line_width=0.5, spots=[0.05, 0.1, 0.2])
    )
    for cell in plan.cells:
        half_x = plan.config.line_length / 2.0
        half_y = plan.config.line_width / 2.0
        for path in cell.paths:
            for x, y in path:
                near_x = abs(x - cell.center[0]) < half_x + cell.spot / 2 - 1e-9
                near_y = abs(y - cell.center[1]) < half_y + cell.spot / 2 - 1e-9
                assert not (near_x and near_y)


def test_inverted_burn_bounds_match_island_plus_border():
    plan = layout.build(
        config(
            invert=True,
            border=0.5,
            line_width=0.5,
            powers=[300.0],
            speeds=[500.0],
            spots=[0.1],
            labels=False,
        )
    )
    min_x, min_y, max_x, max_y = plan.burn_bounds
    assert max_x - min_x == pytest.approx(10.0 + 1.0)
    assert max_y - min_y == pytest.approx(0.5 + 1.0)


def test_sizes_resolve_to_millimetres_or_a_spot_multiple():
    fixed = config(line_width=0.5, border=0.4)
    assert layout.line_width_for(fixed, 0.1) == 0.5
    assert layout.border_for(fixed, 0.1) == 0.4

    scaled = config(line_spots=4.0, border_spots=3.0)
    assert layout.line_width_for(scaled, 0.05) == pytest.approx(0.2)
    assert layout.line_width_for(scaled, 0.15) == pytest.approx(0.6)
    assert layout.border_for(scaled, 0.1) == pytest.approx(0.3)


def test_a_spot_multiple_width_burns_the_same_pass_count_in_every_block():
    spots = [0.05, 0.1, 0.15, 0.2]
    plan = layout.build(config(spots=spots, line_spots=4.0))
    for cell in plan.cells:
        offsets = layout.pass_offsets(cell.width, cell.spot, 0.0)
        assert len(offsets) == 4
        assert cell.width == pytest.approx(4 * cell.spot)


def test_a_spot_multiple_border_burns_the_same_loop_count_in_every_block():
    spots = [0.05, 0.1, 0.15, 0.2]
    plan = layout.build(
        config(spots=spots, invert=True, border_spots=3.0, line_width=0.5)
    )
    for cell in plan.cells:
        assert len(layout.frame_offsets(cell.border, cell.spot, 0.0)) == 3
        assert len(cell.paths) == 3
        # The island is fixed in mm, so it survives at the same width everywhere.
        assert cell.width == 0.5


def test_cells_record_the_size_they_were_built_with():
    plan = layout.build(config(spots=[0.1, 0.2], line_spots=3.0, border_spots=2.0))
    by_spot = {cell.spot: cell for cell in plan.cells}
    assert by_spot[0.1].width == pytest.approx(0.3)
    assert by_spot[0.2].width == pytest.approx(0.6)
    assert by_spot[0.1].border == pytest.approx(0.2)


def test_slot_size_follows_the_largest_block():
    scaled = config(spots=[0.05, 0.2], line_spots=4.0)
    assert layout.slot_size(scaled)[1] == pytest.approx(0.8)
    assert layout.cell_size(scaled, 0.05)[1] == pytest.approx(0.2)


def test_blocks_stay_aligned_when_cells_differ_in_size():
    plan = layout.build(config(spots=[0.05, 0.2], line_spots=4.0))
    first = sorted({cell.center[0] for cell in plan.cells if cell.block == 0})
    second = sorted({cell.center[0] for cell in plan.cells if cell.block == 1})
    assert first == pytest.approx(second)


def test_spot_multiple_lines_keep_their_burned_width():
    plan = layout.build(config(spots=[0.05, 0.2], line_spots=4.0, labels=False))
    for cell in plan.cells:
        ys = [y for path in cell.paths for _, y in path]
        assert max(ys) - min(ys) + cell.spot == pytest.approx(4 * cell.spot)
