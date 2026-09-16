# laser-sweep

GRBL gcode for a UV laser, in the dialect of the known good
`laser-kinda-working.gcode` (`G21`/`G90`, `M4` dynamic power, `S` on the first
cut move of each path).

| Command | Job |
| --- | --- |
| `laser-sweep` | a parameter sweep: spot size against feed rate against power |
| `laser-iso` | PCB isolation straight from a KiCad board or a gerber |
| `laser-backlash` | backlash compensation for a file from another tool |

The sweep finds the settings, `laser-iso` burns the board with them.

## Sweep usage

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

## Sizing in spots

`--line-spots N` replaces `--line-width` and `--border-spots N` replaces
`--border`, sizing them from each block's own spot instead of in millimetres.
Every block then burns the same number of passes, and the physical size scales
with the spot.

```sh
uv run laser-sweep --line-spots 4 --spot 0.05,0.1,0.15
```

This measures the beam directly. `N` passes stepped by an assumed spot `d`
burn a line of `(N - 1) * d + d_true`, so:

```
d_true = measured_width - (N - 1) * d
```

Inverted, `--border-spots N` holds the island at a fixed width while the
frame scales, and the island comes out `width - (d_true - d)` wide. `N` must
be at least 1, since a frame thinner than the beam cannot be cut.

Blocks stay on a common grid: the pitch follows the largest block, so cells
line up across blocks even when they differ in size.

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

## Backlash

`--approach N` rapids to a point `N` mm below and left of every path start
and then rapids into it, so both axes enter each path already moving in the
positive direction. Cell paths begin with a +X move and then a +Y move, so
the sides that define the line or island never follow a direction reversal
and backlash stays out of the measured width. Leave `N` mm of travel free
below and left of the sweep.

```sh
uv run laser-sweep --invert --line-width 0.2 --border-spots 1 --approach 2
```

`--backlash X[,Y]` compensates a measured lost motion instead. The emitter
tracks which way each axis last moved and, after a reversal, shifts every
following coordinate by the backlash so the head lands on the geometry. A
reversal inside a cut is crossed with the beam off: the path ends, a rapid
moves the motor through the dead band while the head stays put, and the cut
resumes with its own `S` and `F`. The job opens with a short rapid to the
origin and then up and right, so both axes start in the positive state, which
is the reference: set the work zero after jogging into it from below left.
Commanded coordinates reach one backlash below and left of the geometry.

```sh
uv run laser-sweep --invert --line-width 0.2 --border-spots 1 --approach 2 --backlash 0.2,0.15
```

Measure the values from a coupon burned with `--approach`: the island length
gives X and the open bottom left corner gives Y, both plus the true spot.

## Existing files

`laser-backlash` applies the same two treatments to a file from another
tool, such as an isolation routing job:

```sh
uv run laser-backlash job.gcode --backlash 0.2,0.15 --approach 2
```

It writes `job.comp.gcode` next to the input (`-o` to choose). Absolute
millimetre `G0`/`G1` moves are rewritten, everything else passes through
untouched, and a cut that reverses an axis is closed, crossed with the beam
off and reopened with the file's own `S` and `F`. Arcs, inches, relative
moves and coordinate offsets are refused; export arcs as line segments.

## PCB isolation

`laser-iso` takes a copper layer and cuts the loops that free every island
from its neighbours. A `.kicad_pcb` goes straight in: `kicad-cli` exports the
gerbers into a temporary directory on the way past.

```sh
uv run laser-iso board.kicad_pcb --spot 0.1 --power 700 --speed 350
uv run laser-iso board-F_Cu.gbr --spot 0.1 --passes 2 --approach 2
```

It writes the same three files as the sweep: the job, a markdown map and an
SVG preview with the copper drawn under the toolpath.

| Option | Meaning |
| --- | --- |
| `--spot` | beam diameter; the first loop runs half a spot off the copper |
| `--passes N` | loops around every feature, stepping out by one spot |
| `--clear W` | mm of copper to clear instead, loops worked out from it |
| `--margin` | extra gap between the copper edge and the first loop |
| `--mirror x` | flip a bottom layer so it can be cut through the board |
| `--anchor` | `corner` (default), `center`, or `keep` the gerber coordinates |
| `--outline auto` | also cut the board profile from Edge.Cuts |
| `--drill auto` | burn a cross or a ring at each hole for a drill to follow |

`--approach` and `--backlash` mean what they do in the sweep, and every loop
starts at its lowest left corner running positive so the compensation has
something to hold onto.

### Copper the beam cannot part

Two islands closer together than twice the first loop offset cannot be
separated: the beam grows through the gap and they stay one net. The job is
still written, with the groups named in the map and circled in the preview,
and the summary says which spot size would fit.

```
warning: 4 group(s) of copper sit closer than the 0.150 mm the first loop
needs, closest 0.298 mm; they stay connected. Lower --spot or open the
layout. See the map.
```

### Layers other than copper

Any gerber works. `--layer` picks which one comes out of a board file, so a
silkscreen or a solder mask layer can be burned with the same machinery.

## Safety

`--s-max` (default 1000) bounds the power. `--fit WxH` refuses to write a job
larger than the given area. Both commands print the extent and write an SVG
preview; check them before running the job. `laser-iso` also names any copper
its spot size cannot part, which is the mistake that costs a board.

## Architecture

Three commands over one emitter. `gcode`, `backlash`, `preview` and `report`
are shared; the sweep builds its geometry, the isolation job reads it out of
a file.

```mermaid
classDiagram
    class cli {
        main(argv)
    }
    class isocli {
        main(argv)
        resolve(args) files
        export_board(board) kicad-cli
    }
    class postprocess {
        compensate(text, backlash, approach)
        main(argv)
    }
    class params {
        parse_range(spec)
    }
    class SweepConfig {
        powers, speeds, spots
        line_length, line_width
        invert, border, frame_order
    }
    class layout {
        build(config) Layout
        pass_offsets(...)
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
    class gerber {
        parse(text) Image
        expand_macro(body, args)
    }
    class Image {
        figures
        bounds
    }
    class excellon {
        parse(text) Drills
        marks(drills, style, spot)
    }
    class geom {
        copper(image) Polygons
        union(a, b)
        offset(paths, delta)
        stroke(paths, width)
        sweep(path, outline)
        weld(paths, gap)
        islands(paths)
    }
    class IsoConfig {
        spot, passes, clear
        overlap, margin, order
        mirror, anchor, origin
    }
    class isolate {
        build(copper, config) IsoPlan
        offsets_for(config)
        as_loop(contour)
        find_bridges(copper, distance)
    }
    class IsoPlan {
        copper, loops, offsets
        islands, bridges
        outline, drills, bounds
    }
    class gcode {
        generate(layout, options) Job
        generate_paths(groups, options) Job
    }
    class Job {
        text, records
        cut_seconds, travel_seconds
    }
    class backlash {
        Compensator
        parse_backlash(text)
    }
    class preview {
        render(layout) svg
        render_iso(plan) svg
    }
    class report {
        render(layout, job) markdown
        render_iso(plan, job) markdown
    }

    cli --> params : ranges
    cli --> SweepConfig : builds
    SweepConfig --> layout
    layout --> Layout : produces
    layout --> font : labels
    Layout --> gcode
    Layout --> preview
    Layout --> report

    isocli --> gerber : copper, profile
    isocli --> excellon : holes
    isocli --> IsoConfig : builds
    gerber --> Image : produces
    Image --> geom
    geom --> isolate : copper polygons
    IsoConfig --> isolate
    isolate --> IsoPlan : produces
    IsoPlan --> gcode
    IsoPlan --> preview
    IsoPlan --> report

    gcode --> Job
    gcode --> backlash : commands
    postprocess --> backlash : commands
    Job --> report
    isolate --> layout : frame_offsets
```

## Development

```sh
uv run pytest
```
