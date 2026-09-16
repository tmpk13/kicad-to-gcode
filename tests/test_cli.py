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
