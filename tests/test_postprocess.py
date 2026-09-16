from pathlib import Path

import pytest

from backlash_sim import assert_cuts_land, cut_targets, geometry_points, moves
from laser_sweep import gcode, layout
from laser_sweep.postprocess import PostprocessError, compensate, main

REFERENCE = Path(__file__).resolve().parent.parent / "laser-kinda-working.gcode"


def test_reference_file_lands_every_cut_where_it_was():
    original = REFERENCE.read_text()
    text, stats = compensate(original, (0.2, 0.15), source="ref")
    assert_cuts_land(text, cut_targets(original), (0.2, 0.15), tol=1e-6)
    assert stats.moves == len(moves(original))
    assert stats.splits > 0
    assert text.startswith("G21")
    assert "; laser-backlash: backlash X 0.2 mm, Y 0.15 mm, from ref" in text


def test_generator_output_compensated_afterwards_matches_the_geometry():
    plan_ = layout.build(
        layout.SweepConfig(
            powers=[200.0], speeds=[250.0], spots=[0.03, 0.04],
            line_length=5.0, line_width=0.2, invert=True, border_spots=1.0,
        )
    )
    plain = gcode.generate(plan_, gcode.GcodeOptions(return_home=False)).text
    text, _ = compensate(plain, (0.2, 0.15), approach=2.0)
    assert_cuts_land(text, geometry_points(plan_), (0.2, 0.15))


def test_split_cut_gets_an_explicit_motion_word_and_its_power_and_feed_back():
    source = "G21\nG90\nM4 S200\nG0 X0 Y0\nG1 X10 Y0 F300\nX10 Y5\nX0 Y5\n"
    text, stats = compensate(source, (0.2, 0.1))
    lines = text.splitlines()
    assert stats.splits == 1
    assert lines[:3] == ["G21", "G90", "M4 S200"]
    assert lines[5] == "G0 X-0.400 Y-0.400"
    assert lines[6] == "G0 X0.000 Y0.000"
    assert lines[8] == "G1 X10.000 Y5.000"
    close = lines.index("M4 S0")
    assert lines[close - 1] == "G1F1"
    assert lines[close + 1] == "G0 X9.800 Y5.000"
    assert lines[close + 2] == "G1F1"
    assert lines[close + 3] == "G1 X-0.200 Y5.000 S200 F300"
    assert_cuts_land(text, cut_targets(source), (0.2, 0.1), tol=1e-9)


def test_reversal_on_the_first_cut_only_needs_a_take_up():
    source = "G90\nG0 X10 Y10\nG1 X0 Y10 F300 S100\n"
    text, stats = compensate(source, (0.2, 0.2))
    assert stats.take_ups == 1 and stats.splits == 0
    assert "S0" not in text.replace("M4 S0", "")
    assert_cuts_land(text, cut_targets(source), (0.2, 0.2), tol=1e-9)


def test_comments_and_other_lines_pass_through_untouched():
    source = "G21 ; mm\n(header)\nG90\nM4 S100\nG0 X1 Y1 ; go\nG1F1\nG1 X2 Y1 S300.00 F500\nG1F1\nM4 S0\nM5\n"
    text, _ = compensate(source, (0.1, 0.1))
    for kept in ("G21 ; mm", "(header)", "G1F1", "M4 S100", "M5"):
        assert kept in text.splitlines()
    assert "G0 X1.000 Y1.000 ; go" in text.splitlines()


def test_approach_alone_stages_every_path_start():
    source = "G90\nG0 X0 Y0\nG1 X5 Y0 F100 S50\nG0 X0 Y10\nG0 X5 Y20\nG1 X9 Y20\n"
    text, stats = compensate(source, (0.0, 0.0), approach=2.0)
    assert stats.staged == 2 and stats.take_ups == 0 and stats.splits == 0
    lines = text.splitlines()
    assert "G0 X-2.000 Y-2.000" in lines
    assert "G0 X3.000 Y18.000" in lines
    assert lines.index("G0 X3.000 Y18.000") == lines.index("G0 X5.000 Y20.000") - 1


@pytest.mark.parametrize(
    "source, expect",
    [
        ("G90\nG0 X0 Y0\nG2 X1 Y1 I0.5 J0\n", "line 3: G2"),
        ("G90\nG91\nG0 X0 Y0\n", "line 2: G91"),
        ("G90\nG1 X1 Y1 F100\n", "first move must be a G0"),
        ("G90\nG0 X1\n", "first move must be a G0"),
        ("G90\nX1 Y1\n", "axis words before any G0 or G1"),
        ("G90\nM4 S100\n", "no X or Y moves"),
    ],
)
def test_unsupported_input_is_refused_with_the_line(source, expect):
    with pytest.raises(PostprocessError) as info:
        compensate(source, (0.1, 0.1))
    assert expect in str(info.value)


def test_cli_writes_next_to_the_input(tmp_path, capsys):
    src = tmp_path / "job.gcode"
    src.write_text("G90\nG0 X0 Y0\nG1 X5 Y0 F100 S50\nG1 X5 Y5\nG1 X0 Y5\n")
    assert main([str(src), "--backlash", "0.2,0.1"]) == 0
    out = tmp_path / "job.comp.gcode"
    assert out.exists()
    assert "; laser-backlash: backlash X 0.2 mm, Y 0.1 mm, from job.gcode" in out.read_text()
    printed = capsys.readouterr().out
    assert "cuts split 1" in printed and "wrote" in printed


def test_cli_refuses_nothing_to_do_and_overwriting(tmp_path, capsys):
    src = tmp_path / "job.gcode"
    src.write_text("G90\nG0 X0 Y0\n")
    with pytest.raises(SystemExit):
        main([str(src)])
    assert "nothing to do" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([str(src), "--backlash", "0.1", "-o", str(src)])
    assert "overwrite" in capsys.readouterr().err
