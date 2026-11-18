"""A small closed unit system: dimensions, named units, and `Quantity`.

Every unit converts exactly to the SI base unit of its dimension through
`si = value * scale + offset`. `degC` is the only offset unit and means an absolute
temperature; temperature differences are not representable. Logarithmic units
(dBm, dB) are deliberately absent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity


class Dimension(StrEnum):
    DIMENSIONLESS = "dimensionless"
    LENGTH = "length"
    TIME = "time"
    FREQUENCY = "frequency"
    TEMPERATURE = "temperature"
    ANGLE = "angle"
    POWER = "power"
    VOLTAGE = "voltage"
    CURRENT = "current"
    RESISTANCE = "resistance"
    CAPACITANCE = "capacitance"


@dataclass(frozen=True)
class Unit:
    dimension: Dimension
    scale: float
    offset: float = 0.0


UNITS: dict[str, Unit] = {
    "1": Unit(Dimension.DIMENSIONLESS, 1.0),
    "m": Unit(Dimension.LENGTH, 1.0),
    "mm": Unit(Dimension.LENGTH, 1e-3),
    "um": Unit(Dimension.LENGTH, 1e-6),
    "nm": Unit(Dimension.LENGTH, 1e-9),
    "s": Unit(Dimension.TIME, 1.0),
    "ms": Unit(Dimension.TIME, 1e-3),
    "us": Unit(Dimension.TIME, 1e-6),
    "ps": Unit(Dimension.TIME, 1e-12),
    "fs": Unit(Dimension.TIME, 1e-15),
    "Hz": Unit(Dimension.FREQUENCY, 1.0),
    "MHz": Unit(Dimension.FREQUENCY, 1e6),
    "GHz": Unit(Dimension.FREQUENCY, 1e9),
    "THz": Unit(Dimension.FREQUENCY, 1e12),
    "K": Unit(Dimension.TEMPERATURE, 1.0),
    "degC": Unit(Dimension.TEMPERATURE, 1.0, 273.15),
    "rad": Unit(Dimension.ANGLE, 1.0),
    "deg": Unit(Dimension.ANGLE, math.pi / 180.0),
    "W": Unit(Dimension.POWER, 1.0),
    "mW": Unit(Dimension.POWER, 1e-3),
    "uW": Unit(Dimension.POWER, 1e-6),
    "V": Unit(Dimension.VOLTAGE, 1.0),
    "mV": Unit(Dimension.VOLTAGE, 1e-3),
    "A": Unit(Dimension.CURRENT, 1.0),
    "mA": Unit(Dimension.CURRENT, 1e-3),
    "ohm": Unit(Dimension.RESISTANCE, 1.0),
    "kohm": Unit(Dimension.RESISTANCE, 1e3),
    "F": Unit(Dimension.CAPACITANCE, 1.0),
    "pF": Unit(Dimension.CAPACITANCE, 1e-12),
    "fF": Unit(Dimension.CAPACITANCE, 1e-15),
}


class Quantity(BaseModel):
    """A finite number with a unit name.

    The unit is not checked on construction, so a payload with a bad unit still loads
    and can be reported with `diagnostics`; arithmetic (`si`, `to`) raises instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(allow_inf_nan=False)
    unit: str

    @property
    def dimension(self) -> Dimension | None:
        """The unit's dimension, or None when the unit is unknown."""
        unit = UNITS.get(self.unit)
        return unit.dimension if unit else None

    @property
    def si(self) -> float:
        """The value in the SI base unit of its dimension."""
        unit = _lookup(self.unit)
        return self.value * unit.scale + unit.offset

    def to(self, unit: str) -> Quantity:
        """Convert to another unit of the same dimension (ValueError otherwise)."""
        source, target = _lookup(self.unit), _lookup(unit)
        if source.dimension is not target.dimension:
            raise ValueError(f"cannot convert {source.dimension} to {target.dimension}")
        return Quantity(value=(self.si - target.offset) / target.scale, unit=unit)

    def diagnostics(
        self,
        expected: Dimension,
        *,
        subject: str | None = None,
        location: tuple[str | int, ...] = (),
    ) -> list[Diagnostic]:
        """Report an unknown unit or a unit of the wrong dimension."""
        actual = self.dimension
        if actual is None:
            code, message = "units.unknown_unit", f"unknown unit {self.unit!r}"
        elif actual is not expected:
            code = "units.dimension_mismatch"
            message = f"unit {self.unit!r} is a {actual} but {expected} is required"
        else:
            return []
        return [
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                message=message,
                subject=subject,
                location=location,
            )
        ]


def _lookup(name: str) -> Unit:
    try:
        return UNITS[name]
    except KeyError:
        raise ValueError(f"unknown unit {name!r}") from None
