import math
import re

import pytest

from backlash_sim import assert_cuts_land, geometry_points, xy
from laser_sweep import gcode, layout


def plan(**overrides) -> layout.Layout:
    base = dict(
        powers=[300.0],
        speeds=[500.0],
        spots=[0.1],
        line_length=10.0,
        line_width=1.0,
        labels=False,
    )
    base.update(overrides)
    return layout.build(layout.SweepConfig(**base))


def generate(plan_, **options) -> gcode.Job:
    return gcode.generate(plan_, gcode.GcodeOptions(**options))


def kinds(text: str) -> list[str]:
    """Classify each motion line so path shape can be compared to the reference."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("G0 "):
            result.append("rapid")
        elif line == "G1F1":
            result.append("feed-guard")
        elif re.fullmatch(r"G1 X\S+ Y\S+ S\S+ F\S+", line):
            result.append("cut-first")
        elif re.fullmatch(r"G1 X\S+ Y\S+", line):
            result.append("cut")
        elif re.fullmatch(r"M[34] S0", line):
            result.append("laser-off")
        else:
            result.append(line.split()[0])
    return result


def test_header_sets_units_and_absolute_mode():
    text = generate(plan()).text
    assert text.splitlines()[:4] == [
        "G21         ; Set units to mm",
        "G90         ; Absolute positioning",
        "G1F1",
        "M4 S0",
    ]


def test_job_ends_with_the_laser_off():
    text = generate(plan(), return_home=False).text
    assert text.strip().splitlines()[-1].startswith("M5")


def test_return_home_moves_back_to_the_origin():
    text = generate(plan(origin=(1.0, 2.0)), return_home=True).text
    assert text.strip().splitlines()[-1] == "G0 X1.000 Y2.000"


def test_each_path_carries_power_and_feed_on_its_first_cut_move_only():
    shape = kinds(generate(plan(), return_home=False).text)
    assert shape[:6] == [
        "G21",
        "G90",
        "feed-guard",
        "laser-off",
        "rapid",
        "feed-guard",
    ]
    body = shape[6 : shape.index("M5")]
    assert body[0] == "cut-first"
    assert body.count("cut-first") == 1
    assert body[-2:] == ["feed-guard", "laser-off"]


def test_path_shape_matches_the_known_good_reference():
    reference = (
        "G0 X-2.59 Y3.24\n"
        "G1F1\n"
        "G1 X-2.51 Y3.27 S300.00 F500\n"
        "G1 X-2.45 Y3.31\n"
        "G1F1\n"
        "M4 S0\n"
    )
    shape = kinds(generate(plan(), return_home=False).text)
    start = shape.index("rapid")
    assert shape[start : start + 4] == kinds(reference)[:4]
    assert shape[shape.index("M5") - 2 : shape.index("M5")] == kinds(reference)[-2:]


def test_power_and_speed_come_from_the_cell():
    text = generate(plan(powers=[640.0], speeds=[250.0])).text
    assert "S640.00 F250" in text


def test_fractional_feed_keeps_two_decimals():
    text = generate(plan(speeds=[333.33])).text
    assert "F333.33" in text


def test_constant_power_mode_switches_the_off_command():
    text = generate(plan(), laser_mode=gcode.M3_CONSTANT).text
    assert "M3 S0" in text
    assert "M4" not in text


def test_coordinate_decimals_are_configurable():
    text = generate(plan(), decimals=1).text
    assert re.search(r"G1 X-?\d+\.\d F", text) or re.search(r"G1 X-?\d+\.\d Y", text)
    assert not re.search(r"X-?\d+\.\d{2}", text)


def test_negative_zero_is_not_emitted():
    text = generate(plan(origin=(0.0, 0.0), anchor="center")).text
    assert "X-0.000" not in text
    assert "Y-0.000" not in text


def test_preamble_and_postamble_are_passed_through():
    text = generate(plan(), preamble=("M7",), postamble=("M9",)).text
    lines = text.splitlines()
    assert lines[4] == "M7"
    assert "M9" in lines[lines.index("M5          ; Switch tool off") - 1]


def test_cut_time_matches_length_over_feed():
    job = generate(plan(speeds=[600.0]))
    assert job.cut_seconds == pytest.approx(job.cut_length / 600.0 * 60.0)


def test_travel_time_uses_the_rapid_rate():
    job = generate(plan(speeds=[500.0], powers=[100.0, 200.0]), rapid_rate=3000.0)
    assert job.travel_seconds == pytest.approx(job.travel_length / 3000.0 * 60.0)
    assert job.seconds > job.cut_seconds


def test_records_cover_every_cell():
    plan_ = plan(powers=[100.0, 200.0], speeds=[300.0, 400.0], spots=[0.1, 0.2])
    job = generate(plan_)
    assert len(job.records) == len(plan_.cells)
    assert all(record["length"] > 0 for record in job.records)
    assert {record["power"] for record in job.records} == {100.0, 200.0}


def test_labels_are_burned_with_their_own_power_and_speed():
    plan_ = plan(powers=[100.0], speeds=[400.0], labels=True, label_power=750.0,
                 label_speed=1200.0)
    text = generate(plan_).text
    assert "S750.00 F1200" in text
    assert "; labels power=750 speed=1200" in text


def test_output_is_ascii_only():
    text = generate(plan(labels=True)).text
    text.encode("ascii")


def test_approach_enters_every_path_from_below_left():
    job = generate(plan(powers=[100.0, 200.0]), approach=2.0, return_home=False)
    rapids = [line for line in job.text.splitlines() if line.startswith("G0 ")]
    assert len(rapids) == 4
    for staging, start in zip(rapids[0::2], rapids[1::2]):
        sx, sy = xy(staging)
        x, y = xy(start)
        assert (x - sx, y - sy) == pytest.approx((2.0, 2.0))
    shape = kinds(job.text)
    assert shape[4:7] == ["rapid", "rapid", "feed-guard"]


def test_approach_travel_counts_both_legs():
    job = generate(plan(powers=[100.0, 200.0]), approach=2.0, return_home=False)
    moves = [
        line for line in job.text.splitlines() if re.match(r"G[01] X", line)
    ]
    expected = 0.0
    position = None
    for line in moves:
        point = xy(line)
        if line.startswith("G0 ") and position is not None:
            expected += math.dist(position, point)
        position = point
    assert job.travel_length == pytest.approx(expected)
    assert job.travel_length > generate(plan(powers=[100.0, 200.0])).travel_length


def test_zero_approach_leaves_the_output_unchanged():
    assert generate(plan(), approach=0.0).text == generate(plan()).text
    assert "Approach:" not in generate(plan()).text


def test_approach_is_recorded_in_the_header():
    assert "Approach:     1.5 mm" in generate(plan(), approach=1.5).text


def test_backlash_lands_every_loop_side_on_the_geometry():
    plan_ = plan(invert=True, border=0.5)
    job = generate(plan_, backlash=(0.2, 0.1), return_home=False)
    assert_cuts_land(job.text, geometry_points(plan_), (0.2, 0.1))


def test_backlash_keeps_a_serpentine_fill_exact():
    plan_ = plan()
    job = generate(plan_, backlash=(0.3, 0.3), return_home=False)
    assert_cuts_land(job.text, geometry_points(plan_), (0.3, 0.3))


def test_backlash_composes_with_the_approach():
    plan_ = plan(invert=True, border=0.1)  # a single loop
    job = generate(plan_, backlash=(0.2, 0.1), approach=2.0, return_home=False)
    assert_cuts_land(job.text, geometry_points(plan_), (0.2, 0.1))
    rapids = [line for line in job.text.splitlines() if line.startswith("G0 ")]
    # state setting pair, staging pair, and a take-up before each of the two
    # sides that reverse an axis
    assert len(rapids) == 6


def test_backlash_reversal_inside_a_cut_is_crossed_with_the_beam_off():
    job = generate(plan(invert=True, border=0.1), backlash=(0.2, 0.1), return_home=False)
    shape = kinds(job.text)
    body = shape[shape.index("cut-first") :]
    # each side of the loop becomes its own path
    assert body.count("cut-first") == 4
    assert body.count("laser-off") == 4
    take_ups = [
        index for index, kind in enumerate(body[:-1])
        if kind == "laser-off" and body[index + 1] == "rapid"
    ]
    assert len(take_ups) == 3


def test_backlash_starts_by_setting_the_positive_state():
    job = generate(plan(origin=(1.0, 2.0)), backlash=(0.2, 0.1))
    rapids = [line for line in job.text.splitlines() if line.startswith("G0 ")]
    assert xy(rapids[0]) == (1.0, 2.0)
    assert xy(rapids[1]) == pytest.approx((1.4, 2.4))


def test_zero_backlash_leaves_the_output_unchanged():
    assert generate(plan(), backlash=(0.0, 0.0)).text == generate(plan()).text
    assert "Backlash:" not in generate(plan()).text


def test_backlash_is_recorded_in_the_header():
    text = generate(plan(), backlash=(0.2, 0.15)).text
    assert "Backlash:     X 0.2 mm, Y 0.15 mm" in text
