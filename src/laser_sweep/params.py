"""Parsing of sweep range specifications given on the command line."""

from __future__ import annotations

import math


class RangeError(ValueError):
    """Raised when a range specification cannot be parsed."""


def parse_range(spec: str, name: str = "range") -> list[float]:
    """Expand a range specification into an ascending list of values.

    Accepted forms:
        "300"                single value
        "100,200,300"        explicit list, kept in the given order
        "100:800:8"          8 values, linearly spaced, both ends included
        "100:800:8:log"      8 values, logarithmically spaced (needs min > 0)
        "100:800:+100"       step form: 100, 200, ... up to and including 800
    """
    text = spec.strip()
    if not text:
        raise RangeError(f"{name}: empty specification")

    if ":" in text and "," in text:
        raise RangeError(f"{name}: mix of ':' and ',' in {spec!r}")

    if "," in text:
        return [_number(part, name) for part in text.split(",") if part.strip()]

    if ":" not in text:
        return [_number(text, name)]

    parts = text.split(":")
    if len(parts) not in (3, 4):
        raise RangeError(
            f"{name}: expected min:max:count[:log] or min:max:+step, got {spec!r}"
        )

    low = _number(parts[0], name)
    high = _number(parts[1], name)
    third = parts[2].strip()
    scale = parts[3].strip().lower() if len(parts) == 4 else "linear"
    if scale not in ("linear", "log"):
        raise RangeError(f"{name}: unknown spacing {parts[3]!r}, use 'linear' or 'log'")

    if third.startswith("+"):
        if scale != "linear":
            raise RangeError(f"{name}: step form does not support log spacing")
        return _by_step(low, high, _number(third[1:], name), name)

    count = int(_number(third, name))
    if count < 1:
        raise RangeError(f"{name}: count must be >= 1, got {count}")
    if count == 1:
        return [low]
    if scale == "log":
        return _log_space(low, high, count, name)
    step = (high - low) / (count - 1)
    return [low + step * i for i in range(count)]


def _by_step(low: float, high: float, step: float, name: str) -> list[float]:
    if step <= 0:
        raise RangeError(f"{name}: step must be > 0")
    if high < low:
        raise RangeError(f"{name}: max must be >= min for the step form")
    values: list[float] = []
    # Walk by index so rounding error cannot accumulate across the range.
    count = int(math.floor((high - low) / step + 1e-9)) + 1
    for i in range(count):
        values.append(low + step * i)
    return values


def _log_space(low: float, high: float, count: int, name: str) -> list[float]:
    if low <= 0 or high <= 0:
        raise RangeError(f"{name}: log spacing needs both ends > 0")
    ratio = math.log(high / low) / (count - 1)
    return [low * math.exp(ratio * i) for i in range(count)]


def _number(text: str, name: str) -> float:
    try:
        return float(text.strip())
    except ValueError as exc:
        raise RangeError(f"{name}: {text.strip()!r} is not a number") from exc


def check_positive(values: list[float], name: str) -> list[float]:
    for value in values:
        if value <= 0:
            raise RangeError(f"{name}: values must be > 0, got {value:g}")
    return values


def check_power(values: list[float], s_max: float) -> list[float]:
    for value in values:
        if value < 0 or value > s_max:
            raise RangeError(
                f"power: {value:g} is outside 0..{s_max:g} (see --s-max)"
            )
    return values
