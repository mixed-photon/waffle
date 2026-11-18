"""Exact conversion between physical lengths and integer database units.

A `Quantity` value is read as the shortest decimal that round-trips its float (the number
as written, `0.1` and not `0.1000000000000000055`), and everything after that is exact
rational arithmetic. A value that is not on the grid is an error unless the caller names a
`Rounding` policy; snapping is reported with its exact error and a warning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import UNITS, Dimension, Quantity
from waffle.contracts.geometry.grid import Grid

_PM_PER_METRE = 10**12


class Rounding(StrEnum):
    EXACT = "exact"  # off-grid values are errors
    NEAREST = "nearest"  # ties go to the even integer
    FLOOR = "floor"
    CEIL = "ceil"


@dataclass(frozen=True)
class Snap:
    """A converted coordinate. `error_pm` is `dbu * dbu_pm - requested`, in exact picometres."""

    dbu: int
    error_pm: Fraction


def pm_per_unit(unit: str) -> int:
    """Picometres in one `unit`; ValueError if it is not a known length unit."""
    entry = UNITS.get(unit)
    if entry is None or entry.dimension is not Dimension.LENGTH:
        raise ValueError(f"{unit!r} is not a length unit")
    return round(entry.scale * _PM_PER_METRE)


def round_fraction(value: Fraction, rounding: Rounding) -> int:
    """`value` as an integer under a policy other than EXACT."""
    match rounding:
        case Rounding.NEAREST:
            return round(value)
        case Rounding.FLOOR:
            return math.floor(value)
        case Rounding.CEIL:
            return math.ceil(value)
        case Rounding.EXACT:
            raise ValueError("EXACT does not round")


def to_dbu(
    quantity: Quantity,
    grid: Grid,
    rounding: Rounding = Rounding.EXACT,
    *,
    subject: str | None = None,
    location: tuple[str | int, ...] = (),
) -> Result[Snap]:
    """Convert a length to database units.

    Errors: a non-length or unknown unit, or (under EXACT) a value off the grid. Any
    other snapping succeeds with a `geometry.grid.snapped` warning stating the error.
    """
    problems = quantity.diagnostics(Dimension.LENGTH, subject=subject, location=location)
    if problems:
        return Result(None, tuple(problems))
    exact = Fraction(repr(quantity.value)) * pm_per_unit(quantity.unit) / grid.dbu_pm
    if exact.denominator == 1:
        return Result.of(Snap(int(exact), Fraction(0)))
    if rounding is Rounding.EXACT:
        return Result(
            None,
            (
                Diagnostic(
                    severity=Severity.ERROR,
                    code="geometry.grid.off_grid",
                    message=f"{quantity.value} {quantity.unit} is not a multiple of the "
                    f"{grid.dbu_pm} pm database unit",
                    subject=subject,
                    location=location,
                ),
            ),
        )
    dbu = round_fraction(exact, rounding)
    error = (dbu - exact) * grid.dbu_pm
    return Result.of(
        Snap(dbu, error),
        [
            Diagnostic(
                severity=Severity.WARNING,
                code="geometry.grid.snapped",
                message=f"{quantity.value} {quantity.unit} snapped to {dbu} dbu "
                f"(error {float(error)} pm, {rounding.value})",
                subject=subject,
                location=location,
            )
        ],
    )


def from_dbu(value: int, grid: Grid, unit: str = "um") -> Quantity:
    """The length of `value` database units, as the float nearest to the exact value."""
    return Quantity(value=dbu_to_float(value, grid, unit), unit=unit)


def dbu_to_float(value: int, grid: Grid, unit: str = "um") -> float:
    """`value` database units in `unit`, correctly rounded to a float."""
    return value * grid.dbu_pm / pm_per_unit(unit)
