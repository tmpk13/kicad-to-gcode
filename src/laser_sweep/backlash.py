"""Direction tracking and coordinate shifting that hide backlash from the head."""

from __future__ import annotations

Point = tuple[float, float]


def format_coord(value: float, decimals: int) -> str:
    """Fixed decimals, with a sign that rounded away dropped.

    -0.000 reads badly on a machine display and confuses diffs.
    """
    text = f"{value:.{decimals}f}"
    if float(text) == 0.0:
        text = f"{0.0:.{decimals}f}"
    return text


def parse_backlash(text: str) -> Point:
    """One value for both axes, or X,Y."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) not in (1, 2):
        raise ValueError(f"backlash expects X or X,Y, got {text!r}")
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"backlash: {text!r} is not a number") from exc
    if any(value < 0 for value in values):
        raise ValueError("backlash must be >= 0")
    if len(values) == 1:
        values.append(values[0])
    return values[0], values[1]


class Compensator:
    """Turns the positions the head should reach into the ones to command.

    The positive direction is the reference: a point reached moving positive
    is commanded as is. After a reversal the head trails the motor by the
    backlash, so a point reached moving negative is commanded one backlash
    further on, and the motor travels the dead band plus the move.
    """

    def __init__(self, backlash: Point = (0.0, 0.0)) -> None:
        if any(value < 0 for value in backlash):
            raise ValueError("backlash must be >= 0")
        self.backlash = backlash
        self.position: Point | None = None
        # +1, -1, or 0 while the axis has not moved yet.
        self.direction = [0, 0]

    @property
    def active(self) -> bool:
        return any(value > 0 for value in self.backlash)

    def commanded(self, target: Point) -> Point:
        return (
            target[0] - (self.backlash[0] if self.direction[0] < 0 else 0.0),
            target[1] - (self.backlash[1] if self.direction[1] < 0 else 0.0),
        )

    def reversals(self, target: Point) -> list[int]:
        """Axes whose known direction flips on a move to `target`."""
        if self.position is None:
            return []
        flipped = []
        for axis in (0, 1):
            delta = target[axis] - self.position[axis]
            if abs(delta) > 1e-9:
                sign = 1 if delta > 0 else -1
                if self.direction[axis] and sign != self.direction[axis]:
                    flipped.append(axis)
        return flipped

    def move(self, target: Point) -> Point:
        """Record a move to `target` and return the coordinates to command."""
        if self.position is not None:
            for axis in (0, 1):
                delta = target[axis] - self.position[axis]
                if abs(delta) > 1e-9:
                    self.direction[axis] = 1 if delta > 0 else -1
        self.position = target
        return self.commanded(target)

    def take_up(self, target: Point) -> Point | None:
        """Where a beam-off rapid goes before a cut to `target` reverses an axis.

        The rapid targets the point the head already stands on, expressed in
        the new direction state, so the motor crosses the dead band and the
        head stays put. None when nothing reverses or nothing is compensated.
        """
        if not self.active:
            return None
        flipped = self.reversals(target)
        if not flipped:
            return None
        for axis in flipped:
            self.direction[axis] = -self.direction[axis]
        return self.commanded(self.position)
