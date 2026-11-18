"""Parameter schemas and values for the three parameter roles.

* design parameters: choose the physical device; stored on a `ResolvedDesign`.
* operating conditions: how a model is evaluated (wavelength, temperature, bias);
  declared by a model and supplied per analysis, never stored on a design.
* fitted coefficients: numbers extracted from data; stored on a model revision.

Each role has its own value type, and a schema only accepts values of its own role.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.units import Dimension, Quantity

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _error(code: str, message: str, subject: str | None, *location: str | int) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject, location=location
    )


class ParameterSpec(BaseModel):
    """One named, dimensioned parameter with optional default and inclusive bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    dimension: Dimension
    default: Quantity | None = None
    minimum: Quantity | None = None
    maximum: Quantity | None = None
    description: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not IDENTIFIER.fullmatch(self.name):
            raise ValueError(f"invalid parameter name {self.name!r}")
        given = {"default": self.default, "minimum": self.minimum, "maximum": self.maximum}
        for label, quantity in given.items():
            if quantity is not None and (found := quantity.diagnostics(self.dimension)):
                raise ValueError(f"{label} of {self.name}: {found[0].message}")
        if self.default is not None and self.out_of_bounds(self.default):
            raise ValueError(f"default of {self.name} violates its bounds")
        if self.minimum and self.maximum and self.minimum.si > self.maximum.si:
            raise ValueError(f"minimum of {self.name} exceeds its maximum")
        return self

    def out_of_bounds(self, quantity: Quantity) -> bool:
        """True if a quantity of the right dimension lies outside the bounds."""
        value = quantity.si
        below = (
            self.minimum and value < self.minimum.si and not math.isclose(value, self.minimum.si)
        )
        above = (
            self.maximum and value > self.maximum.si and not math.isclose(value, self.maximum.si)
        )
        return bool(below or above)


class ParameterValues(BaseModel):
    """Concrete named quantities. Subclasses fix the role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: dict[str, Quantity] = {}


class DesignValues(ParameterValues):
    role: Literal["design"] = "design"


class OperatingValues(ParameterValues):
    role: Literal["operating"] = "operating"


class FittedCoefficients(ParameterValues):
    role: Literal["fitted"] = "fitted"


class ParameterSchema(BaseModel):
    """An ordered set of specs. Subclasses fix the role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameters: tuple[ParameterSpec, ...] = ()

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [p.name for p in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("parameter names must be unique")
        return self

    def check(self, supplied: ParameterValues, subject: str | None = None) -> list[Diagnostic]:
        """Report role mismatch, unknown, missing, wrong-unit, and out-of-bounds values.

        A parameter with a default is not missing. Diagnostics locate the parameter as
        `("values", name)`.
        """
        role = type(self).model_fields["role"].default
        if (supplied_role := getattr(supplied, "role", None)) != role:
            return [
                _error(
                    "params.role_mismatch",
                    f"{supplied_role} values supplied where {role} values are required",
                    subject,
                )
            ]
        found: list[Diagnostic] = []
        known = {p.name: p for p in self.parameters}
        for name in supplied.values:
            if name not in known:
                found.append(
                    _error(
                        "params.unknown",
                        f"unknown {role} parameter {name!r}",
                        subject,
                        "values",
                        name,
                    )
                )
        for spec in self.parameters:
            quantity = supplied.values.get(spec.name)
            if quantity is None:
                if spec.default is None:
                    found.append(
                        _error(
                            "params.missing",
                            f"missing {role} parameter {spec.name!r}",
                            subject,
                            "values",
                            spec.name,
                        )
                    )
                continue
            unit_problems = quantity.diagnostics(
                spec.dimension, subject=subject, location=("values", spec.name)
            )
            found.extend(unit_problems)
            if not unit_problems and spec.out_of_bounds(quantity):
                found.append(
                    _error(
                        "params.out_of_bounds",
                        f"{spec.name} = {quantity.value} {quantity.unit} is outside its bounds",
                        subject,
                        "values",
                        spec.name,
                    )
                )
        return found

    def with_defaults(self, supplied: Mapping[str, Quantity]) -> dict[str, Quantity]:
        """`supplied` plus the default of every parameter it omits."""
        filled = dict(supplied)
        for spec in self.parameters:
            if spec.name not in filled and spec.default is not None:
                filled[spec.name] = spec.default
        return filled


class DesignParameterSchema(ParameterSchema):
    role: Literal["design"] = "design"


class OperatingConditionSchema(ParameterSchema):
    role: Literal["operating"] = "operating"
