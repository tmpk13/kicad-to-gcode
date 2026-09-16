import pytest

from laser_sweep.cli import main


def run(tmp_path, *args):
    out = tmp_path / "sweep.gcode"
    code = main(["-o", str(out), *args])
    return code, out


def test_default_run_writes_gcode_map_and_preview(tmp_path, capsys):
    code, out = run(tmp_path, "--power", "300", "--speed", "500", "--spot", "0.1")
    assert code == 0
    assert out.exists()
    assert (tmp_path / "sweep.gcode.map.md").exists()
    assert (tmp_path / "sweep.gcode.preview.svg").exists()
    assert "S300.00 F500" in out.read_text()
    assert "wrote" in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, capsys):
    code, out = run(tmp_path, "--dry-run")
    assert code == 0
    assert not out.exists()
    assert "dry run" in capsys.readouterr().out


def test_no_map_and_no_preview_suppress_the_extras(tmp_path):
    _, out = run(tmp_path, "--no-map", "--no-preview")
    assert out.exists()
    assert not (tmp_path / "sweep.gcode.map.md").exists()
    assert not (tmp_path / "sweep.gcode.preview.svg").exists()


def test_map_and_preview_paths_can_be_chosen(tmp_path):
    run(
        tmp_path,
        "--map",
        str(tmp_path / "notes.md"),
        "--preview",
        str(tmp_path / "look.svg"),
    )
    assert (tmp_path / "notes.md").exists()
    assert (tmp_path / "look.svg").exists()


def test_output_directory_is_created(tmp_path):
    out = tmp_path / "deep" / "nested" / "sweep.gcode"
    assert main(["-o", str(out), "--dry-run"]) == 0
    assert main(["-o", str(out)]) == 0
    assert out.exists()


def test_every_combination_appears_once(tmp_path):
    _, out = run(
        tmp_path, "--power", "200,400", "--speed", "300,600", "--spot", "0.1,0.2"
    )
    text = out.read_text()
    for spot in ("0.1", "0.2"):
        for power in ("200", "400"):
            for speed in ("300", "600"):
                marker = f"; cell spot={spot} power={power} speed={speed}"
                assert text.count(marker) == 1


def test_fit_rejects_an_oversized_sweep(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--fit", "5x5")
    assert exc.value.code == 2


def test_fit_can_be_forced(tmp_path, capsys):
    code, out = run(tmp_path, "--fit", "5x5", "--force")
    assert code == 0
    assert out.exists()
    assert "warning" in capsys.readouterr().err


def test_fit_accepts_a_sweep_that_does_fit(tmp_path):
    code, _ = run(tmp_path, "--power", "300", "--speed", "500", "--fit", "100x100")
    assert code == 0


@pytest.mark.parametrize(
    "args",
    [
        ("--power", "2000"),
        ("--speed", "0"),
        ("--spot", "-0.1"),
        ("--power", "nope"),
        ("--line-width", "0"),
        ("--passes", "0"),
        ("--overlap", "1"),
        ("--fit", "wide"),
    ],
)
def test_bad_arguments_exit_with_usage_error(tmp_path, args):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *args)
    assert exc.value.code == 2


def test_s_max_widens_the_allowed_power_range(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, "--power", "1200")
    code, _ = run(tmp_path, "--power", "1200", "--s-max", "2000")
    assert code == 0


def test_summary_reports_size_and_estimate(tmp_path, capsys):
    run(tmp_path, "--dry-run", "--power", "300", "--speed", "500", "--spot", "0.1")
    out = capsys.readouterr().out
    assert "cells      1" in out
    assert "size" in out and "estimate" in out


def test_generated_files_are_ascii(tmp_path):
    _, out = run(tmp_path, "--spot", "0.05,0.1")
    for path in (out, tmp_path / "sweep.gcode.map.md",
                 tmp_path / "sweep.gcode.preview.svg"):
        path.read_text(encoding="ascii")


def test_invert_writes_an_island_sweep(tmp_path):
    _, out = run(
        tmp_path, "--invert", "--power", "300", "--speed", "500", "--spot", "0.1"
    )
    text = out.read_text()
    assert "; Island:" in text
    assert "border 0.4 mm" in text
    assert "S300.00 F500" in text


def test_invert_reports_the_island_in_the_map(tmp_path):
    run(tmp_path, "--invert", "--power", "300", "--speed", "500", "--spot", "0.1")
    text = (tmp_path / "sweep.gcode.map.md").read_text()
    assert "| Island |" in text
    assert "Measured island" in text
    assert "left standing" in text


def test_invert_grows_the_sweep_by_the_border(tmp_path, capsys):
    common = ["--dry-run", "--no-labels", "--power", "300", "--speed", "500",
              "--spot", "0.1", "--line-length", "10", "--line-width", "1"]
    run(tmp_path, *common)
    plain = _size(capsys.readouterr().out)
    run(tmp_path, *common, "--invert", "--border", "0.5")
    inverted = _size(capsys.readouterr().out)

    # A solid line is a stroked centerline, so its ends run a beam radius past
    # the nominal length; the island's outer edge lands on the border exactly.
    assert plain == pytest.approx((10.0 + 0.1, 1.0))
    assert inverted == pytest.approx((10.0 + 1.0, 1.0 + 1.0))


def _size(summary: str) -> tuple[float, float]:
    line = next(line for line in summary.splitlines() if line.startswith("size"))
    width, _, height, _ = line.split()[1:]
    return float(width), float(height)


def test_border_narrower_than_the_widest_spot_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--invert", "--border", "0.1", "--spot", "0.05,0.1,0.3")
    assert exc.value.code == 2


def test_border_is_only_checked_when_inverting(tmp_path):
    code, _ = run(tmp_path, "--border", "0.01", "--spot", "0.2")
    assert code == 0


def test_frame_order_is_accepted(tmp_path):
    _, out = run(tmp_path, "--invert", "--frame-order", "outside-in", "--spot", "0.1")
    assert "loops outside-in" in out.read_text()


def test_line_spots_sizes_the_line_from_each_blocks_spot(tmp_path):
    _, out = run(
        tmp_path, "--line-spots", "4", "--spot", "0.05,0.1", "--power", "300",
        "--speed", "500",
    )
    text = out.read_text()
    assert "; Line:" in text
    assert "4 spots (0.2, 0.4 mm)" in text


def test_border_spots_sizes_the_frame_from_each_blocks_spot(tmp_path):
    _, out = run(
        tmp_path, "--invert", "--border-spots", "3", "--spot", "0.05,0.1",
        "--power", "300", "--speed", "500",
    )
    text = out.read_text()
    assert "border 3 spots (0.15, 0.3 mm)" in text


def test_spot_multiples_show_up_per_block_in_the_map(tmp_path):
    run(tmp_path, "--line-spots", "4", "--spot", "0.05,0.1", "--power", "300",
        "--speed", "500")
    text = (tmp_path / "sweep.gcode.map.md").read_text()
    assert "### Spot 0.05 mm (line 0.2 mm)" in text
    assert "### Spot 0.1 mm (line 0.4 mm)" in text
    assert "Target line" in text


def test_line_width_and_line_spots_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--line-width", "1", "--line-spots", "4")
    assert exc.value.code == 2


def test_border_and_border_spots_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--invert", "--border", "0.5", "--border-spots", "3")
    assert exc.value.code == 2


def test_border_spots_under_one_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--invert", "--border-spots", "0.5")
    assert exc.value.code == 2


def test_line_spots_must_be_positive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--line-spots", "0")
    assert exc.value.code == 2


def test_border_spots_sidesteps_the_millimetre_border_check(tmp_path):
    # 0.4 mm of border cannot hold a 0.5 mm beam, but one spot always can.
    with pytest.raises(SystemExit):
        run(tmp_path, "--invert", "--spot", "0.5")
    code, _ = run(tmp_path, "--invert", "--spot", "0.5", "--border-spots", "1")
    assert code == 0
