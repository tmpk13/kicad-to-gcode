"""Parse the emitted gcode back and check the burn it describes."""

from __future__ import annotations

import re

import pytest

from laser_sweep import gcode, layout

WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")

# Coordinates are emitted at GcodeOptions.decimals, so a measured span carries
# a rounding error at each end.
QUANTUM = 2e-3


def parse(text: str) -> list[dict]:
    """Return the cut segments a GRBL controller would run, with modal S and F."""
    x = y = 0.0
    power = 0.0
    feed = 0.0
    segments: list[dict] = []
    for raw in text.splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        words = dict(WORD.findall(line))
        if "M" in words:
            # M3/M4 carry the S word that arms or disarms the beam.
            power = float(words.get("S", power))
            if words["M"] == "5":
                power = 0.0
            continue
        if "S" in words:
            power = float(words["S"])
        if "F" in words:
            feed = float(words["F"])
        if "G" not in words:
            continue
        move = words["G"]
        nx = float(words.get("X", x))
        ny = float(words.get("Y", y))
        if move == "1" and ("X" in words or "Y" in words) and power > 0:
            segments.append(
                {"x0": x, "y0": y, "x1": nx, "y1": ny, "power": power, "feed": feed}
            )
        if move in ("0", "1"):
            x, y = nx, ny
    return segments


def build(**overrides) -> tuple[layout.Layout, gcode.Job]:
    base = dict(
        powers=[200.0, 800.0],
        speeds=[300.0, 900.0],
        spots=[0.1],
        line_length=10.0,
        line_width=1.0,
        labels=False,
    )
    base.update(overrides)
    plan = layout.build(layout.SweepConfig(**base))
    return plan, gcode.generate(plan, gcode.GcodeOptions())


def test_no_cutting_happens_with_the_beam_disarmed():
    _, job = build()
    lines = job.text.splitlines()
    # The last rapid is the return home, after M5.
    for index, line in enumerate(lines[: lines.index("M5          ; Switch tool off")]):
        if line.startswith("G0 "):
            # Every rapid is followed by the feed guard, never by a live cut.
            assert lines[index + 1] == "G1F1"


def test_each_cell_burns_a_band_of_the_requested_width():
    plan, job = build(line_width=1.4, spots=[0.2])
    spot = 0.2
    groups: dict[tuple[float, float], list[dict]] = {}
    for segment in parse(job.text):
        groups.setdefault((segment["power"], segment["feed"]), []).append(segment)

    assert len(groups) == len(plan.cells)
    for segments in groups.values():
        ys = [value for s in segments for value in (s["y0"], s["y1"])]
        assert max(ys) - min(ys) + spot == pytest.approx(1.4)
        xs = [value for s in segments for value in (s["x0"], s["x1"])]
        assert max(xs) - min(xs) == pytest.approx(10.0)


def test_swept_beam_leaves_no_gap_across_the_line():
    _, job = build(line_width=1.0, spots=[0.15], powers=[500.0], speeds=[400.0])
    spot = 0.15
    ys = sorted({round(s["y0"], 6) for s in parse(job.text) if s["y0"] == s["y1"]})
    for previous, current in zip(ys, ys[1:]):
        assert current - previous <= spot + 1e-9


def test_parsed_power_and_feed_match_the_sweep_ranges():
    plan, job = build()
    seen = {(s["power"], s["feed"]) for s in parse(job.text)}
    expected = {
        (power, speed)
        for power in plan.config.powers
        for speed in plan.config.speeds
    }
    assert seen == expected


def test_cut_length_from_the_gcode_matches_the_estimate():
    _, job = build()
    total = sum(
        ((s["x1"] - s["x0"]) ** 2 + (s["y1"] - s["y0"]) ** 2) ** 0.5
        for s in parse(job.text)
    )
    assert total == pytest.approx(job.cut_length, rel=1e-3)


def test_labels_burn_outside_every_cell_band():
    plan, job = build(labels=True, label_power=900.0, label_speed=1500.0)
    label_segments = [
        s for s in parse(job.text) if (s["power"], s["feed"]) == (900.0, 1500.0)
    ]
    assert label_segments
    for cell in plan.cells:
        top = cell.center[1] + plan.config.line_width / 2.0
        bottom = cell.center[1] - plan.config.line_width / 2.0
        left = cell.center[0] - plan.config.line_length / 2.0
        right = cell.center[0] + plan.config.line_length / 2.0
        for segment in label_segments:
            inside_x = left <= segment["x0"] <= right
            inside_y = bottom <= segment["y0"] <= top
            assert not (inside_x and inside_y)


def test_inverted_gcode_never_cuts_inside_the_island():
    plan, job = build(
        invert=True, border=0.5, line_width=0.6, spots=[0.2], line_length=8.0
    )
    half_x = plan.config.line_length / 2.0
    half_y = plan.config.line_width / 2.0
    radius = 0.2 / 2.0
    for segment in parse(job.text):
        cell = min(
            plan.cells,
            key=lambda c: abs(c.center[0] - segment["x0"])
            + abs(c.center[1] - segment["y0"]),
        )
        # An axis aligned segment is its own bounding box; grow it by the beam.
        lo_x = min(segment["x0"], segment["x1"]) - radius - cell.center[0]
        hi_x = max(segment["x0"], segment["x1"]) + radius - cell.center[0]
        lo_y = min(segment["y0"], segment["y1"]) - radius - cell.center[1]
        hi_y = max(segment["y0"], segment["y1"]) + radius - cell.center[1]
        overlaps_x = lo_x < half_x - 1e-9 and hi_x > -half_x + 1e-9
        overlaps_y = lo_y < half_y - 1e-9 and hi_y > -half_y + 1e-9
        assert not (overlaps_x and overlaps_y)


def test_inverted_gcode_clears_the_full_border():
    plan, job = build(
        invert=True,
        border=0.5,
        line_width=0.6,
        spots=[0.1],
        line_length=8.0,
        powers=[300.0],
        speeds=[500.0],
        labels=False,
    )
    cell = plan.cells[0]
    radius = 0.1 / 2.0
    ys = [v for s in parse(job.text) for v in (s["y0"], s["y1"])]
    xs = [v for s in parse(job.text) for v in (s["x0"], s["x1"])]
    assert max(ys) - cell.center[1] + radius == pytest.approx(0.6 / 2 + 0.5)
    assert max(xs) - cell.center[0] + radius == pytest.approx(8.0 / 2 + 0.5)


def test_inverted_cells_still_carry_their_own_power_and_feed():
    plan, job = build(invert=True, border=0.5, line_width=0.6)
    seen = {(s["power"], s["feed"]) for s in parse(job.text)}
    expected = {
        (power, speed)
        for power in plan.config.powers
        for speed in plan.config.speeds
    }
    assert seen == expected


def test_a_spot_multiple_width_gives_every_block_the_same_pass_count():
    spots = [0.05, 0.1, 0.2]
    plan, job = build(
        powers=[300.0], speeds=[500.0], spots=spots, line_spots=4.0, labels=False
    )
    for cell in plan.cells:
        # Cells sit in separate blocks, so a Y window picks one out on its own.
        rows = {
            round(s["y0"], 6)
            for s in parse(job.text)
            if abs(s["y0"] - cell.center[1]) < cell.width
        }
        assert len(rows) == 4
        assert max(rows) - min(rows) + cell.spot == pytest.approx(
            4 * cell.spot, abs=QUANTUM
        )


def test_a_spot_multiple_border_keeps_the_island_the_same_in_every_block():
    spots = [0.05, 0.1, 0.2]
    plan, job = build(
        powers=[300.0],
        speeds=[500.0],
        spots=spots,
        invert=True,
        line_width=0.4,
        border_spots=3.0,
        labels=False,
    )
    segments = parse(job.text)
    for cell in plan.cells:
        near = [
            s for s in segments if abs(s["y0"] - cell.center[1]) < 0.4 + cell.border
        ]
        inner = min(abs(s["y0"] - cell.center[1]) for s in near)
        assert inner - cell.spot / 2 == pytest.approx(0.2, abs=QUANTUM)
        outer = max(abs(s["y0"] - cell.center[1]) for s in near)
        assert outer + cell.spot / 2 == pytest.approx(
            0.2 + 3 * cell.spot, abs=QUANTUM
        )
