# Completed

## laser-sweep generator (2026-09-15)

- uv managed Python project, stdlib only at runtime, pytest for tests.
- Range parser: single value, comma list, `min:max:count[:log]`,
  `min:max:+step`. Used for all three sweep axes.
- Sweep axes: power (S), speed (F), spot size. Spot size gets one block per
  value; power is rows, speed is columns.
- Lines of settable width, filled with parallel passes stepped by the spot
  size, with `--overlap`, `--passes`, serpentine or unidirectional fill.
- Placement: gaps, block gaps, block columns, origin, center or corner anchor,
  `--fit WxH` guard with `--force` override.
- Burned labels in a seven-segment stroke font, prefixed `P`, `F`, `d`, with
  their own power and speed.
- Emitter matches the known good EasyTrace dialect (`G1F1` feed guards, S and
  F on the first cut move, `M4 S0` per path, `M5` at the end), with `M3`
  constant power, decimals, preamble and postamble options.
- Outputs: gcode, markdown map (cell coordinates, worksheet, mermaid gantt of
  estimated burn time), SVG preview.
- 100 tests, including a round trip that parses the emitted gcode back and
  checks the burned band width, gap freedom and power/feed pairs.

## Inverted lines (2026-09-15)

- `--invert` clears a `--border` frame around an uncut island, with
  `--line-width` as the width left standing. Concentric closed loops, offsets
  running from half a spot outside the island to half a spot inside the
  border, so the island and the cleared band are both exact.
- `--frame-order inside-out|outside-in`.
- Border narrower than the widest swept spot is refused with a message naming
  both values.
- Cell footprint, grid pitch, fit check, preview, gcode header and map
  worksheet all follow the inverted geometry.
- 24 further tests, including gcode round trips proving no cut segment (grown
  by the beam radius) enters the island and the cleared band reaches the full
  border.

## Sizing in spots (2026-09-16)

- `--line-spots N` and `--border-spots N` size the line width and the cleared
  border from each block's own spot, so every block burns the same number of
  passes or loops and the coupon reads out the true beam diameter.
- Mutually exclusive with `--line-width` and `--border`; `--border-spots`
  under 1 is refused, and it bypasses the millimetre border check since one
  spot of border is always cuttable.
- Grid pitch follows the largest block (`layout.slot_size`), so blocks stay
  aligned when their cells differ in size.
- Cells record their resolved width and border; gcode header, map headings and
  a new worksheet target column show what each block works out as.
- 17 further tests.

## Approach direction (2026-09-16)

- `--approach N` rapids to a point N mm below and left of every path start
  and then into it, so each path begins with both axes moving positive and
  the island-defining +X and +Y sides never follow a direction reversal.
  Recorded in the gcode header. 6 further tests.

## Backlash compensation (2026-09-16)

- `--backlash X[,Y]` shifts coordinates after every reversal by the measured
  lost motion, crosses reversals inside a cut with the beam off by closing
  the path and rapiding the motor through the dead band, and opens the job
  with a rapid that puts both axes in the positive reference state. Header
  and map record it. Simulated dead band test proves every cut lands on the
  geometry with the head never still under power. 11 further tests.
