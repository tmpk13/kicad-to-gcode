"""Reading RS-274X: apertures, macros, regions, arcs and polarity."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from laser_sweep import gerber

DATA = Path(__file__).parent / "data"

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\nG01*\n"


def parse(body: str) -> gerber.Image:
    return gerber.parse(HEADER + body + "M02*\n")


def test_format_spec_scales_coordinates():
    image = parse("%ADD10C,0.100000*%\nD10*\nX1000000Y2000000D03*\n")
    fill = image.figures[0]
    xs = [point[0] for _, contour in fill.parts for point in contour]
    ys = [point[1] for _, contour in fill.parts for point in contour]
    assert sum(xs) / len(xs) == pytest.approx(1.0, abs=1e-6)
    assert sum(ys) / len(ys) == pytest.approx(2.0, abs=1e-6)


def test_inch_units_convert_to_mm():
    image = gerber.parse(
        "%FSLAX24Y24*%\n%MOIN*%\n%LPD*%\n%ADD10C,0.1*%\nD10*\nX10000Y0D03*\nM02*\n"
    )
    xs = [point[0] for _, contour in image.figures[0].parts for point in contour]
    assert sum(xs) / len(xs) == pytest.approx(25.4, abs=1e-3)


def test_circle_aperture_flash_is_round():
    image = parse("%ADD10C,1.0*%\nD10*\nX0Y0D03*\n")
    (_, contour), = image.figures[0].parts
    radii = [math.hypot(x, y) for x, y in contour]
    assert min(radii) == pytest.approx(0.5, abs=gerber.CURVE_TOLERANCE)
    assert max(radii) == pytest.approx(0.5, abs=1e-9)


def test_rectangle_aperture_flash_has_four_corners():
    image = parse("%ADD10R,2.0X1.0*%\nD10*\nX0Y0D03*\n")
    (_, contour), = image.figures[0].parts
    assert len(contour) == 4
    assert min(x for x, _ in contour) == pytest.approx(-1.0)
    assert max(y for _, y in contour) == pytest.approx(0.5)


def test_aperture_hole_becomes_an_exposure_off_part():
    image = parse("%ADD10C,1.0X0.4*%\nD10*\nX0Y0D03*\n")
    exposures = [exposure for exposure, _ in image.figures[0].parts]
    assert exposures == [True, False]


def test_a_holed_aperture_cannot_stroke():
    with pytest.raises(gerber.GerberError, match="cannot stroke"):
        parse("%ADD10C,1.0X0.4*%\nD10*\nX0Y0D02*\nX1000000Y0D01*\n")


def test_draw_becomes_one_stroke_with_the_aperture_width():
    image = parse("%ADD10C,0.2*%\nD10*\nX0Y0D02*\nX1000000Y0D01*\nX1000000Y1000000D01*\n")
    (draw,) = image.figures
    assert isinstance(draw, gerber.Draw)
    assert draw.width == pytest.approx(0.2)
    assert draw.points == ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))


def test_a_move_breaks_a_stroke_in_two():
    image = parse(
        "%ADD10C,0.2*%\nD10*\nX0Y0D02*\nX1000000Y0D01*\n"
        "X2000000Y0D02*\nX3000000Y0D01*\n"
    )
    assert [len(figure.points) for figure in image.figures] == [2, 2]


def test_region_is_one_filled_contour():
    image = parse(
        "G36*\nX0Y0D02*\nX1000000Y0D01*\nX1000000Y1000000D01*\nX0Y1000000D01*\n"
        "X0Y0D01*\nG37*\n"
    )
    (fill,) = image.figures
    assert isinstance(fill, gerber.Fill)
    (exposure, contour), = fill.parts
    assert exposure
    assert (1.0, 1.0) in contour


def test_clear_polarity_is_recorded():
    image = parse(
        "%ADD10C,1.0*%\nD10*\nX0Y0D03*\n%LPC*%\nX0Y0D03*\n"
    )
    assert [figure.dark for figure in image.figures] == [True, False]


def test_multi_quadrant_arc_sweeps_a_quarter_circle():
    image = parse(
        "%ADD10C,0.1*%\nD10*\nG75*\nX1000000Y0D02*\nG03*\nX0Y1000000I-1000000J0D01*\n"
    )
    (draw,) = image.figures
    assert draw.points[-1] == pytest.approx((0.0, 1.0))
    radii = [math.hypot(x, y) for x, y in draw.points]
    assert max(abs(radius - 1.0) for radius in radii) < gerber.CURVE_TOLERANCE


def test_full_circle_arc_returns_to_its_start():
    image = parse(
        "%ADD10C,0.1*%\nD10*\nG75*\nX1000000Y0D02*\nG02*\nX1000000Y0I-1000000J0D01*\n"
    )
    (draw,) = image.figures
    assert draw.points[0] == pytest.approx(draw.points[-1])
    assert len(draw.points) > 8


def test_single_quadrant_arc_picks_the_short_way():
    image = parse(
        "%ADD10C,0.1*%\nD10*\nG74*\nX1000000Y0D02*\nG03*\nX0Y1000000I1000000J0D01*\n"
    )
    (draw,) = image.figures
    assert draw.points[-1] == pytest.approx((0.0, 1.0))
    assert all(math.hypot(x, y) == pytest.approx(1.0, abs=1e-3) for x, y in draw.points)


def test_macro_variables_and_arithmetic():
    image = parse(
        "%AMBOX*\n$4=$1x2*\n21,1,$4,$2,0,0,0*%\n%ADD10BOX,1.0X0.5*%\nD10*\nX0Y0D03*\n"
    )
    (_, contour), = image.figures[0].parts
    assert max(x for x, _ in contour) == pytest.approx(1.0)
    assert max(y for _, y in contour) == pytest.approx(0.25)


def test_macro_exposure_off_cuts_a_hole():
    image = parse(
        "%AMRING*\n1,1,2.0,0,0*\n1,0,1.0,0,0*%\n%ADD10RING*%\nD10*\nX0Y0D03*\n"
    )
    assert [exposure for exposure, _ in image.figures[0].parts] == [True, False]


def test_step_repeat_copies_the_block():
    image = parse(
        "%ADD10C,1.0*%\nD10*\n%SRX2Y1I5.0J0*%\nX0Y0D03*\n%SR*%\n"
    )
    assert len(image.figures) == 2
    centers = sorted(
        sum(x for x, _ in figure.parts[0][1]) / len(figure.parts[0][1])
        for figure in image.figures
    )
    assert centers[1] - centers[0] == pytest.approx(5.0, abs=1e-6)


def test_unknown_aperture_is_refused_with_its_code():
    with pytest.raises(gerber.GerberError, match="D42"):
        parse("D42*\nX0Y0D03*\n")


def test_incremental_coordinates_are_refused():
    with pytest.raises(gerber.GerberError, match="incremental"):
        gerber.parse("%FSLIX46Y46*%\n%MOMM*%\nM02*\n")


def test_mirror_command_is_refused():
    with pytest.raises(gerber.GerberError, match="mirror"):
        parse("%MIA1B0*%\n")


def test_real_board_file_reads():
    image = gerber.read(DATA / "board-F_Cu.gbr")
    assert image.file_function.startswith("Copper")
    assert len(image.figures) > 20
    min_x, min_y, max_x, max_y = image.bounds
    assert max_x - min_x > 5.0
    assert max_y - min_y > 5.0


def test_an_inch_polygon_aperture_keeps_its_rotation():
    """The rotation is degrees, so only the diameter may be scaled to mm."""
    image = gerber.parse(
        "%FSLAX24Y24*%\n%MOIN*%\n%LPD*%\n%ADD10P,1.0X4X45*%\nD10*\nX0Y0D03*\nM02*\n"
    )
    (_, contour), = image.figures[0].parts
    # A four sided aperture has a vertex on +X; turned 45 degrees they all sit
    # on the diagonals, an inch diameter out.
    radii = [math.hypot(x, y) for x, y in contour]
    assert max(radii) == pytest.approx(25.4 / 2.0, abs=1e-6)
    assert all(abs(abs(x) - abs(y)) < 1e-9 for x, y in contour)


def test_an_inch_circle_is_still_drawn_finely():
    """The chord tolerance is in mm, so an inch file gets the same fineness."""
    metric = parse("%ADD10C,25.4*%\nD10*\nX0Y0D03*\n")
    inch = gerber.parse(
        "%FSLAX24Y24*%\n%MOIN*%\n%LPD*%\n%ADD10C,1.0*%\nD10*\nX0Y0D03*\nM02*\n"
    )
    assert len(inch.figures[0].parts[0][1]) == len(metric.figures[0].parts[0][1])
