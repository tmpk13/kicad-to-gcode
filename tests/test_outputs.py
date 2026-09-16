import xml.etree.ElementTree as ET

from laser_sweep import gcode, layout, preview, report


def plan() -> layout.Layout:
    return layout.build(
        layout.SweepConfig(
            powers=[200.0, 400.0],
            speeds=[300.0, 600.0],
            spots=[0.1, 0.2],
            line_length=8.0,
            line_width=0.8,
        )
    )


def test_preview_is_well_formed_svg_sized_in_mm():
    root = ET.fromstring(preview.render(plan()))
    assert root.tag.endswith("svg")
    assert root.get("width", "").endswith("mm")
    assert root.get("height", "").endswith("mm")


def test_preview_draws_a_stroke_per_spot_size():
    svg = preview.render(plan())
    assert 'stroke-width="0.1000"' in svg
    assert 'stroke-width="0.2000"' in svg


def test_preview_travel_can_be_hidden():
    assert "stroke-dasharray" in preview.render(plan(), show_travel=True)
    assert "stroke-dasharray" not in preview.render(plan(), show_travel=False)


def test_map_lists_every_cell_in_the_worksheet():
    plan_ = plan()
    job = gcode.generate(plan_, gcode.GcodeOptions())
    text = report.render(plan_, job, "sweep.gcode")
    body = text.split("## Worksheet")[1]
    rows = [line for line in body.splitlines() if line.startswith("| ") and "|" in line]
    # One header row, one separator row, then one row per cell.
    assert len(rows) == len(plan_.cells) + 2


def test_map_has_a_grid_per_spot_size_and_a_timeline():
    plan_ = plan()
    job = gcode.generate(plan_, gcode.GcodeOptions())
    text = report.render(plan_, job, "sweep.gcode")
    assert "### Spot 0.1 mm" in text
    assert "### Spot 0.2 mm" in text
    assert "```mermaid" in text and "gantt" in text


def test_timeline_durations_accumulate_without_gaps():
    plan_ = plan()
    job = gcode.generate(plan_, gcode.GcodeOptions())
    text = report.render(plan_, job, "sweep.gcode")
    tasks = [
        line for line in text.splitlines() if ":b" in line and line.endswith("s")
    ]
    cursor = 0
    for task in tasks:
        _, start, duration = task.rsplit(",", 2)
        assert int(start) == cursor
        cursor += int(duration.strip().rstrip("s"))
