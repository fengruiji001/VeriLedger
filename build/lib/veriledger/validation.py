"""Shared validation of public simulation inputs."""

from __future__ import annotations

import math
from numbers import Integral, Real
from typing import TypeGuard


def has_document_id(value: object) -> TypeGuard[str]:
    """A missing or whitespace-only identifier cannot support invoice evidence."""
    return isinstance(value, str) and bool(value.strip())


def integer(name: str, value, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def finite(name: str, value, minimum: float | None = None, maximum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and float(value) < minimum or maximum is not None and float(value) > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def periods(bounds: list[tuple[int, int]]) -> None:
    if not bounds:
        raise ValueError("period_bounds must not be empty")
    previous = None
    for a, b in bounds:
        integer("period start", a)
        integer("period end", b)
        if b < a or previous is not None and a != previous + 1:
            raise ValueError("period_bounds must be non-empty, contiguous and ordered")
        previous = b
