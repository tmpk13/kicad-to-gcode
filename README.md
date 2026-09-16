# laser-sweep

Generates GRBL gcode that burns a parameter sweep for a UV laser: spot size
against feed rate against power, drawn as lines of a settable width.

Output matches the dialect of the known good `laser-kinda-working.gcode`
(`G21`/`G90`, `M4` dynamic power, `S` on the first cut move of each path).

## Usage

```sh
uv run laser-sweep --power 200:800:4 --speed 300:900:4 --spot 0.05,0.1,0.15
```

Writes three files next to `--output` (default `out/sweep.gcode`):

| File | Purpose |
| --- | --- |
| `sweep.gcode` | the job |
| `sweep.gcode.map.md` | cell coordinates, worksheet, time estimate |
| `sweep.gcode.preview.svg` | layout check before burning |

Add `--dry-run` to print the summary without writing anything.

## Ranges

Every sweep range accepts the same forms:

| Form | Meaning |
| --- | --- |
| `300` | one value |
| `100,200,300` | explicit list |
| `100:800:8` | 8 values, linear, ends included |
| `100:800:8:log` | 8 values, logarithmic |
| `100:800:+100` | steps of 100 |

## Reading the coupon

Each spot size gets its own block, labelled `d<spot>` above its top left
corner. Inside a block, power (`P`) labels the rows and increases upwards,
feed rate (`F`) labels the columns and increases to the right. Labels are
burned with the middle of each range unless `--label-power` /
`--label-speed` say otherwise.

Line width is filled with parallel passes stepped over by the spot size, so
the pass centers span `width - spot` and the beam radius covers the rest.
`--overlap` shrinks the step, `--fill unidirectional` lifts the beam between
passes instead of turning it around.

A solid line is a stroked centerline: `--line-width` is burned exactly, but
the ends run half a spot past `--line-length`.

## Inverted lines

`--invert` swaps the burn: each cell clears a `--border` frame around an
uncut island, and `--line-width` becomes the width left standing. This is the
isolation routing case, where the feature that matters is the copper that
survives.

```sh
uv run laser-sweep --invert --line-width 0.3 --border 0.4 --spot 0.05,0.1,0.15
```

The frame is cut as concentric closed loops. The innermost loop sits half a
spot outside the island, so its beam edge stops on the island boundary; the
outermost stops half a spot inside the border, so the cleared band is exactly
`--border` wide. `--frame-order outside-in` reverses the loops. `--fill` does
not apply.

A border narrower than the widest spot in the sweep is refused: that frame
cannot be cut without eating into the island.

## Safety

`--s-max` (default 1000) bounds the power range. `--fit WxH` refuses to write
a sweep larger than the given area. Check the preview SVG and the extent in
the summary before running the job.

## Architecture

```mermaid
classDiagram
    class cli {
        main(argv)
    }
    class params {
        parse_range(spec)
    }
    class SweepConfig {
        powers, speeds, spots
        line_length, line_width
        overlap, passes, fill
        invert, border, frame_order
    }
    class layout {
        build(config) Layout
        cell_paths(...)
        fill_line(...)
        pass_offsets(...)
        frame_loops(...)
        frame_offsets(...)
    }
    class Layout {
        cells, label_paths
        bounds, burn_bounds
        travel_order()
    }
    class font {
        text_paths(text, ...)
    }
    class gcode {
        generate(layout, options) Job
    }
    class Job {
        text, records
        cut_seconds, travel_seconds
    }
    class preview {
        render(layout) svg
    }
    class report {
        render(layout, job) markdown
    }
    cli --> params : ranges
    cli --> SweepConfig : builds
    SweepConfig --> layout
    layout --> Layout : produces
    layout --> font : labels
    Layout --> gcode
    Layout --> preview
    gcode --> Job
    Job --> report
    Layout --> report
```

## Development

```sh
uv run pytest
```
