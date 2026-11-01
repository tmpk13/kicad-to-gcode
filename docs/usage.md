# laser-sweep usage

Each command's `--help` lists every option; NOTES.md explains why each
treatment works the way it does.

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

## Reading the coupon

Each spot size gets its own block, labelled `d<spot>` above its top left
corner. Inside a block, power (`P`) labels the rows and increases upwards,
feed rate (`F`) labels the columns and increases to the right. Labels are
burned with the middle of each range unless `--label-power` /
`--label-speed` say otherwise.

## Sizing in spots

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

## Inverted lines

```sh
uv run laser-sweep --invert --line-width 0.3 --border 0.4 --spot 0.05,0.1,0.15
```

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

`--backlash X[,Y]` compensates a measured lost motion instead.

```sh
uv run laser-sweep --invert --line-width 0.2 --border-spots 1 --approach 2 --backlash 0.2,0.15
```

Measure the values from a coupon burned with `--approach`: the island length
gives X and the open bottom left corner gives Y, both plus the true spot.

### Sweeping the compensation

```sh
uv run laser-sweep --invert --line-width 0.25 --border-spots 2 --approach 2 \
    --spot 0.1 --power 500 --speed 400 --backlash-sweep 0:0.3:4
```

Blocks are labelled `b<value>` next to the spot size. Read the block whose
island measures its target length and whose bottom left corner closes: less
than the true lost motion leaves the island short and the corner open, more
runs it long. It is the same reading in both axes at once, since X shows up
in the length and Y in the corner.

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

```sh
uv run laser-iso board.kicad_pcb --spot 0.1 --power 700 --speed 350
uv run laser-iso board-F_Cu.gbr --spot 0.1 --passes 2 --approach 2
```

It writes the same three files as the sweep: the job, a markdown map and an
SVG preview with the copper drawn under the toolpath.

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
