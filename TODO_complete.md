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
