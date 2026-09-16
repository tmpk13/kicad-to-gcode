import pytest

from laser_sweep import font


def test_every_label_character_has_a_glyph():
    for char in "0123456789.-PFd":
        strokes, advance = font.glyph(char)
        assert advance > 0
        assert all(len(stroke) >= 2 for stroke in strokes)


def test_unknown_character_raises():
    with pytest.raises(KeyError):
        font.glyph("@")


def test_chaining_joins_a_closed_digit_into_one_stroke():
    strokes, _ = font.glyph("0")
    assert len(strokes) == 1
    assert strokes[0][0] == strokes[0][-1]


def test_chaining_beats_one_path_per_segment():
    # "8" lights all seven segments; the middle bar cannot join the outer loop.
    strokes, _ = font.glyph("8")
    assert len(strokes) == 2


def test_text_paths_respect_the_left_bottom_anchor():
    paths = font.text_paths("1", 5.0, 2.0, 2.0, anchor="lb")
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    assert min(xs) >= 5.0 - 1e-9
    assert min(ys) == pytest.approx(2.0)
    assert max(ys) == pytest.approx(4.0)


def test_text_paths_respect_the_right_center_anchor():
    paths = font.text_paths("12", 0.0, 0.0, 2.0, anchor="rc")
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    assert max(xs) == pytest.approx(0.0)
    assert min(ys) == pytest.approx(-1.0)
    assert max(ys) == pytest.approx(1.0)


def test_text_width_grows_with_characters_and_height():
    assert font.text_width("11", 2.0) > font.text_width("1", 2.0)
    assert font.text_width("1", 4.0) == pytest.approx(2 * font.text_width("1", 2.0))


def test_bad_anchor_raises():
    with pytest.raises(ValueError):
        font.text_paths("1", 0.0, 0.0, 1.0, anchor="xy")


@pytest.mark.parametrize(
    ("value", "text"),
    [(500.0, "500"), (0.1, "0.1"), (0.15, "0.15"), (1000.0, "1000"), (0.0, "0")],
)
def test_number_formatting_is_compact(value, text):
    assert font.format_number(value) == text
