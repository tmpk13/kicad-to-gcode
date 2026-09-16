import pytest

from laser_sweep.params import RangeError, check_positive, check_power, parse_range


def test_single_value():
    assert parse_range("300") == [300.0]


def test_explicit_list_keeps_order():
    assert parse_range("300,100,200") == [300.0, 100.0, 200.0]


def test_linear_range_includes_both_ends():
    assert parse_range("100:800:8") == pytest.approx(
        [100, 200, 300, 400, 500, 600, 700, 800]
    )


def test_linear_range_of_one_is_the_low_end():
    assert parse_range("100:800:1") == [100.0]


def test_step_form_includes_the_top_when_it_lands_on_it():
    assert parse_range("100:800:+100") == pytest.approx(
        [100, 200, 300, 400, 500, 600, 700, 800]
    )


def test_step_form_stops_below_an_unreachable_top():
    assert parse_range("100:750:+100") == pytest.approx(
        [100, 200, 300, 400, 500, 600, 700]
    )


def test_log_range_is_geometric():
    values = parse_range("100:1600:5:log")
    assert values == pytest.approx([100, 200, 400, 800, 1600])


def test_descending_linear_range():
    assert parse_range("800:200:3") == pytest.approx([800, 500, 200])


@pytest.mark.parametrize(
    "spec",
    ["", "abc", "1:2", "1:2:3:4:5", "1:2:0", "1,2:3", "1:2:3:cubic", "1:2:+0"],
)
def test_bad_specs_raise(spec):
    with pytest.raises(RangeError):
        parse_range(spec)


def test_log_range_needs_positive_ends():
    with pytest.raises(RangeError):
        parse_range("0:100:4:log")


def test_check_positive_rejects_zero():
    with pytest.raises(RangeError):
        check_positive([1.0, 0.0], "speed")


def test_check_power_bounds_against_s_max():
    check_power([0.0, 1000.0], 1000.0)
    with pytest.raises(RangeError):
        check_power([1200.0], 1000.0)
    with pytest.raises(RangeError):
        check_power([-1.0], 1000.0)
