# laser-sweep

GRBL gcode for a UV laser, in the dialect of the known good
`laser-kinda-working.gcode` (`G21`/`G90`, `M4` dynamic power, `S` on the first
cut move of each path).
The sweep finds the settings, `laser-iso` burns the board with them.

- `mise run sweep`: a parameter sweep: spot size against feed rate against power
- `mise run iso <board.kicad_pcb | layer.gbr>`: PCB isolation straight from a KiCad board or a gerber
- `mise run backlash <job.gcode>`: backlash compensation for a file from another tool
- `mise run test`: run the tests

Options after the task name go to the command; `mise run sweep -- --help` lists them.
`mise tasks` lists the rest. Usage: docs/usage.md. Design notes: NOTES.md. Components: ARCHITECTURE.md.
