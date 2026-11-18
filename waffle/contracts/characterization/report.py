"""Bounded validation evidence; untested quantities remain explicit."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.foundation.records import Record
from waffle.contracts.results.artifact import ReferenceField


class Comparison(BaseModel):
    """One measured scalar discrepancy with its acceptance threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    quantity: str
    frequency_hz: float = Field(gt=0)
    error: float = Field(ge=0)
    tolerance: float = Field(ge=0)
    unit: str
    comparison: Literal[
        "analytic_reference",
        "grid_convergence",
        "time_convergence",
        "boundary_convergence",
        "held_out",
    ]

    @property
    def passed(self) -> bool:
        return self.error <= self.tolerance


class ValidationReport(Record):
    """The tested samples, reference procedure, and limitations of a claim."""

    kind: Literal["validation_report"] = "validation_report"
    schema_version: Literal[1] = 1
    procedure: str = Field(min_length=1)
    procedure_version: str = Field(min_length=1)
    reference_description: str = Field(min_length=1)
    inputs: tuple[ReferenceField, ...] = ()
    comparisons: tuple[Comparison, ...] = Field(min_length=1)
    observed_channels: tuple[tuple[str, str], ...] = ()
    uncomputed_channels: tuple[tuple[str, str], ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)
    interpolation: Literal["none_exact_samples"] = "none_exact_samples"

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        if set(self.observed_channels) & set(self.uncomputed_channels):
            raise ValueError("observed and uncomputed channels overlap")
        return self

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.comparisons)
