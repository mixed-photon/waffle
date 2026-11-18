"""Optical material models and the wavelength ranges they are valid over.

Conventions: the complex refractive index is `n + i*k` with `k >= 0` for loss. Every
`Material` declares the wavelength range its model is valid over and a source string.
Records only describe; evaluation lives in `waffle.technology.materials`.

Room for later schema versions (not modelled now): electrical and thermal properties will
be added as sibling fields of `optical`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.component.parameters import IDENTIFIER
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.units import Dimension, Quantity


class WavelengthRange(BaseModel):
    """A closed vacuum-wavelength interval `[minimum, maximum]`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum: Quantity
    maximum: Quantity

    def diagnostics(self, subject: str, location: tuple[str | int, ...]) -> list[Diagnostic]:
        """Unit problems, or a non-positive or empty interval."""
        found = [
            *self.minimum.diagnostics(
                Dimension.LENGTH, subject=subject, location=(*location, "minimum")
            ),
            *self.maximum.diagnostics(
                Dimension.LENGTH, subject=subject, location=(*location, "maximum")
            ),
        ]
        if not found and not 0 < self.minimum.si < self.maximum.si:
            found.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="technology.wavelength_range_invalid",
                    message="wavelength range needs 0 < minimum < maximum",
                    subject=subject,
                    location=location,
                )
            )
        return found

    def contains(self, wavelength: Quantity) -> bool:
        """Whether `wavelength` lies in the interval (units must be valid)."""
        return self.minimum.si <= wavelength.si <= self.maximum.si

    def covers(self, other: WavelengthRange) -> bool:
        """Whether `other` lies entirely inside this interval (units must be valid)."""
        return self.minimum.si <= other.minimum.si and other.maximum.si <= self.maximum.si


class ConstantIndex(BaseModel):
    """Wavelength-independent index `n + i*k`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["constant"] = "constant"
    n: float = Field(gt=0, allow_inf_nan=False)
    k: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class Sellmeier(BaseModel):
    """`n^2 = 1 + sum_i b_i * L^2 / (L^2 - c_i)` with `L` the wavelength in micrometres.

    `c_um2` holds the resonance terms in square micrometres. Lossless (`k = 0`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["sellmeier"] = "sellmeier"
    b: tuple[float, ...] = Field(min_length=1)
    c_um2: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _terms(self) -> Self:
        if len(self.b) != len(self.c_um2):
            raise ValueError("b and c_um2 must have the same length")
        if any(c < 0 for c in self.c_um2):
            raise ValueError("c_um2 terms must be non-negative")
        return self


class Tabulated(BaseModel):
    """`n` and `k` sampled at increasing wavelengths (micrometres), interpolated linearly.

    The declared `interpolation` is the only way values between samples are defined.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tabulated"] = "tabulated"
    interpolation: Literal["linear"] = "linear"
    wavelength_um: tuple[float, ...] = Field(min_length=2)
    n: tuple[float, ...]
    k: tuple[float, ...]

    @model_validator(mode="after")
    def _samples(self) -> Self:
        if not len(self.wavelength_um) == len(self.n) == len(self.k):
            raise ValueError("wavelength_um, n and k must have the same length")
        if self.wavelength_um[0] <= 0 or any(
            a >= b for a, b in zip(self.wavelength_um, self.wavelength_um[1:], strict=False)
        ):
            raise ValueError("wavelength_um must be positive and strictly increasing")
        if any(v <= 0 for v in self.n) or any(v < 0 for v in self.k):
            raise ValueError("n must be positive and k non-negative")
        return self

    @property
    def span(self) -> WavelengthRange:
        return WavelengthRange(
            minimum=Quantity(value=self.wavelength_um[0], unit="um"),
            maximum=Quantity(value=self.wavelength_um[-1], unit="um"),
        )


type OpticalModel = Annotated[ConstantIndex | Sellmeier | Tabulated, Field(discriminator="type")]


class Material(BaseModel):
    """A named optical material. `source` cites where the model's numbers come from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=IDENTIFIER.pattern)
    optical: OpticalModel
    validity: WavelengthRange
    source: str = Field(min_length=1)
