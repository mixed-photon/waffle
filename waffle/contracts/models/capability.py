"""Typed model capability, applicability, and evidence of domain coverage."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.foundation.units import Dimension, Quantity


class Analysis(StrEnum):
    OPTICAL_SCATTERING = "optical.frequency_domain.scattering"
    ELECTRICAL_DC = "electrical.dc"


class OpticalScattering(BaseModel):
    """Executable complex amplitude response at one optical frequency."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["optical_scattering"] = "optical_scattering"
    analysis: Literal[Analysis.OPTICAL_SCATTERING] = Analysis.OPTICAL_SCATTERING
    frequency_parameter: str = "frequency"
    normalization: Literal["power_wave"] = "power_wave"
    # A complete matrix is not implied: only listed output/excitation pairs exist.
    observed_channels: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def _unique_channels(self) -> Self:
        if len(set(self.observed_channels)) != len(self.observed_channels):
            raise ValueError("observed channels must be unique")
        return self


class ElectricalDescriptor(BaseModel):
    """Shared model metadata for a future electrical evaluator; not executable here."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["electrical_descriptor"] = "electrical_descriptor"
    analysis: Literal[Analysis.ELECTRICAL_DC] = Analysis.ELECTRICAL_DC


type Capability = Annotated[
    OpticalScattering | ElectricalDescriptor, Field(discriminator="type")
]


class ExtrapolationPolicy(StrEnum):
    REJECT = "reject"
    ALLOW_WITH_WARNING = "allow_with_warning"


class NumericInterval(BaseModel):
    """Inclusive interval of one parameter, in a consistent physical dimension."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    minimum: Quantity
    maximum: Quantity

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if (
            self.minimum.dimension is None
            or self.minimum.dimension != self.maximum.dimension
        ):
            raise ValueError("interval bounds must have known, equal dimensions")
        if self.minimum.si > self.maximum.si:
            raise ValueError("interval minimum exceeds maximum")
        return self

    def contains(self, value: Quantity) -> bool:
        return (
            value.dimension == self.minimum.dimension
            and self.minimum.si <= value.si <= self.maximum.si
        )


class AxisCoverage(BaseModel):
    """Sampled points and validated interpolation bounds are independent claims.

    None means unknown; an empty sample list means known to have no samples.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    parameter: str
    sampled: tuple[Quantity, ...] | None = None
    validated: NumericInterval | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        dimensions = {value.dimension for value in self.sampled or ()}
        if None in dimensions or len(dimensions) > 1:
            raise ValueError("sampled values must share one known dimension")
        if (
            self.validated is not None
            and dimensions
            and self.validated.minimum.dimension not in dimensions
        ):
            raise ValueError("sampled and validated dimensions differ")
        return self


class Uncertainty(BaseModel):
    """An unknown estimate is distinct from a measured zero estimate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["unknown", "bounded"] = "unknown"
    absolute_bound: Quantity | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "unknown" and self.absolute_bound is not None:
            raise ValueError("unknown uncertainty cannot have a bound")
        if self.status == "bounded" and self.absolute_bound is None:
            raise ValueError("bounded uncertainty needs a bound")
        if self.absolute_bound is not None and (
            self.absolute_bound.dimension is not Dimension.DIMENSIONLESS
            or self.absolute_bound.si < 0
        ):
            raise ValueError(
                "scattering amplitude uncertainty needs a nonnegative dimensionless bound"
            )
        return self


class Applicability(BaseModel):
    """Parameter restrictions and declared behavior outside sampled coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    design_ranges: dict[str, NumericInterval] = Field(default_factory=dict)
    operating_ranges: dict[str, NumericInterval] = Field(default_factory=dict)
    axes: tuple[AxisCoverage, ...] = ()
    extrapolation: ExtrapolationPolicy = ExtrapolationPolicy.REJECT
    uncertainty: Uncertainty = Uncertainty()

    @model_validator(mode="after")
    def _unique_axes(self) -> Self:
        names = [axis.parameter for axis in self.axes]
        if len(names) != len(set(names)):
            raise ValueError("coverage axis names must be unique")
        return self
