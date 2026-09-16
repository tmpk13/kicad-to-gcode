"""laser-iso end to end: the file it writes, and the burn that file describes."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from backlash_sim import assert_cuts_land
from laser_sweep import geom, gerber, isolate
from laser_sweep.isocli import main

DATA = Path(__file__).parent / "data"
COPPER = DATA / "board-F_Cu.gbr"
WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")

# One machine step on the reference config, and the coordinate quantum the
# file is written at: a vertex can round half a quantum towards the copper.
STEP = 0.004
QUANTUM = 0.001


def run(tmp_path, *args):
    out = tmp_path / "board.gcode"
    code = main([str(COPPER), "-o", str(out), *args])
    return code, out


def cuts(text: str) -> list[list[tuple[float, float]]]:
    """The paths a controller would burn: moves at non zero power, joined up."""
    x = y = 0.0
    power = 0.0
    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for raw in text.splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        words = dict(WORD.findall(line))
        if "M" in words:
            power = 0.0 if words["M"] == "5" else float(words.get("S", power))
            continue
        if "S" in words:
            power = float(words["S"])
        if "G" not in words:
            continue
        move = words["G"]
        nx = float(words.get("X", x))
        ny = float(words.get("Y", y))
        if move == "1" and ("X" in words or "Y" in words) and power > 0:
            if not current:
                current = [(x, y)]
            current.append((nx, ny))
        elif current:
            paths.append(current)
            current = []
        if move in ("0", "1"):
            x, y = nx, ny
    if current:
        paths.append(current)
    return paths


def test_it_writes_gcode_a_map_and_a_preview(tmp_path, capsys):
    code, out = run(tmp_path, "--spot", "0.2")
    assert code == 0
    assert out.exists()
    assert (tmp_path / "board.gcode.map.md").exists()
    assert (tmp_path / "board.gcode.preview.svg").exists()
    assert "wrote" in capsys.readouterr().out


def test_the_dialect_matches_the_sweep(tmp_path):
    _, out = run(tmp_path, "--spot", "0.2", "--power", "600", "--speed", "450")
    text = out.read_text()
    assert text.startswith("G21")
    assert "G90         ; Absolute positioning" in text
    assert "S600.00 F450" in text
    assert text.count("M4 S0") > 1
    assert "M5          ; Switch tool off" in text


def test_dry_run_writes_nothing(tmp_path, capsys):
    code, out = run(tmp_path, "--dry-run")
    assert code == 0
    assert not out.exists()
    assert "dry run" in capsys.readouterr().out


def test_no_map_and_no_preview_suppress_the_extras(tmp_path):
    _, out = run(tmp_path, "--no-map", "--no-preview")
    assert out.exists()
    assert not (tmp_path / "board.gcode.map.md").exists()
    assert not (tmp_path / "board.gcode.preview.svg").exists()


def test_the_corner_anchor_keeps_the_job_off_negative_coordinates(tmp_path):
    _, out = run(tmp_path, "--spot", "0.15")
    values = [
        float(value)
        for letter, value in WORD.findall(out.read_text())
        if letter in "XY"
    ]
    assert min(values) >= 0.0


def test_an_outline_adds_a_phase(tmp_path):
    _, out = run(tmp_path, "--outline", "auto", "--outline-passes", "2")
    text = out.read_text()
    assert "board outline pass 1" in text
    assert "board outline pass 2" in text


def test_drill_marks_add_a_phase(tmp_path):
    _, out = run(tmp_path, "--drill", "auto")
    assert "drill marks (3 holes)" in out.read_text()


def test_passes_show_their_offset_from_the_copper(tmp_path):
    _, out = run(tmp_path, "--spot", "0.2", "--passes", "2")
    text = out.read_text()
    assert "isolation loop 1 at 0.100 mm" in text
    assert "isolation loop 2 at 0.300 mm" in text


def test_a_fit_that_fails_refuses_to_write(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(tmp_path, "--fit", "5x5")
    assert "does not fit" in capsys.readouterr().err


def test_force_writes_despite_the_fit(tmp_path, capsys):
    code, out = run(tmp_path, "--fit", "5x5", "--force")
    assert code == 0
    assert out.exists()
    assert "does not fit" in capsys.readouterr().err


def test_a_spot_too_wide_to_isolate_warns(tmp_path, capsys):
    run(tmp_path, "--spot", "0.6")
    assert "stay connected" in capsys.readouterr().err


def test_a_missing_file_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nothing.gbr")])
    assert "does not exist" in capsys.readouterr().err


def test_power_over_s_max_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(tmp_path, "--power", "2000")
    assert "s-max" in capsys.readouterr().err


def test_approach_and_backlash_reach_the_file(tmp_path):
    _, out = run(tmp_path, "--approach", "2", "--backlash", "0.2,0.15")
    text = out.read_text()
    assert "Approach:" in text
    assert "Backlash:     X 0.2 mm, Y 0.15 mm" in text


def test_the_burn_the_file_describes_never_touches_the_copper(tmp_path):
    spot = 0.2
    _, out = run(tmp_path, "--spot", str(spot), "--passes", "2")
    plan = isolate.build(
        geom.copper(gerber.read(COPPER)), isolate.IsoConfig(spot=spot, passes=2)
    )
    burn = geom.stroke(cuts(out.read_text()), spot - STEP - QUANTUM)
    assert geom.area(geom.intersection(burn, plan.copper)) == pytest.approx(0.0)


def test_every_loop_in_the_file_closes_on_itself(tmp_path):
    _, out = run(tmp_path, "--spot", "0.2")
    paths = cuts(out.read_text())
    assert paths
    for path in paths:
        assert path[0] == pytest.approx(path[-1], abs=1e-3)


BOARD = Path(__file__).parent / "data" / "board.kicad_pcb"


@pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="kicad-cli is not installed"
)
def board_copy(tmp_path) -> Path:
    """kicad-cli writes a .kicad_prl beside the board, so work on a copy."""
    copy = tmp_path / BOARD.name
    shutil.copy(BOARD, copy)
    return copy


def test_a_board_file_is_exported_and_isolated(tmp_path):
    out = tmp_path / "from-board.gcode"
    code = main(
        [
            str(board_copy(tmp_path)), "-o", str(out), "--spot", "0.2",
            "--gerber-dir", str(tmp_path / "gbr"),
        ]
    )
    assert code == 0
    assert "Isolation for" in out.read_text()
    assert (tmp_path / "gbr").is_dir()


@pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="kicad-cli is not installed"
)
def test_a_board_file_brings_its_own_outline(tmp_path):
    """The profile is exported with the copper, so --outline auto needs no path."""
    out = tmp_path / "whole.gcode"
    code = main(
        [
            str(board_copy(tmp_path)), "-o", str(out), "--spot", "0.2",
            "--outline", "auto",
            "--gerber-dir", str(tmp_path / "gbr"),
        ]
    )
    assert code == 0
    assert "board outline pass 1" in out.read_text()
    assert list((tmp_path / "gbr").glob("*Edge_Cuts*"))


def test_backlash_compensated_cuts_land_on_the_copper_edge(tmp_path):
    """A head with the measured dead band still burns the planned loops."""
    spot, backlash = 0.2, (0.2, 0.15)
    _, out = run(tmp_path, "--spot", str(spot), "--backlash", "0.2,0.15")
    plan = isolate.build(
        geom.copper(gerber.read(COPPER)), isolate.IsoConfig(spot=spot)
    )
    wanted = [tuple(point) for loop in plan.loops for point in loop.points[1:]]
    assert_cuts_land(out.read_text(), wanted, backlash)


def test_the_extent_never_reports_a_rounded_away_minus(tmp_path, capsys):
    """A corner anchored job lands on 0.00, not -0.00, wherever the float lands."""
    run(tmp_path, "--spot", "0.1", "--dry-run")
    assert "-0.00" not in capsys.readouterr().out
