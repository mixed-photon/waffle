"""Uniform-guide coherent monitor ratios, without invented matrix entries."""

from __future__ import annotations

import cmath
from typing import Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result

EXTRACTION_VERSION = "uniform_profile_ratio_v1"


class TransferSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    frequency_hz: float = Field(gt=0)
    real: float
    imag: float
    profile_residual: float = Field(ge=0)

    @property
    def amplitude(self) -> complex:
        return complex(self.real, self.imag)


class WaveguideEvidence(BaseModel):
    """An exact-sample field transfer; modal qualification is a separate step."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    extraction: Literal["uniform_profile_ratio_v1", "modal_overlap_ratio_v1"] = (
        EXTRACTION_VERSION
    )
    time_convention: Literal["exp(-i omega t)"] = "exp(-i omega t)"
    normalization: Literal["same_source_profile_ratio", "same_mode_overlap_ratio"] = (
        "same_source_profile_ratio"
    )
    upstream_plane_m: float
    downstream_plane_m: float
    field_component: Literal["Ez", "Hz"]
    output_channel: str
    excitation_channel: str
    grid_spacing_m: float = Field(gt=0)
    time_step_s: float = Field(gt=0)
    steps: int = Field(gt=0)
    samples: tuple[TransferSample, ...] = Field(min_length=1)
    raw_artifacts: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "Raw field ratio does not separate forward and backward waves.",
        "No interpolation, reverse excitation, reflections, or MMI claim.",
    )

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.downstream_plane_m <= self.upstream_plane_m:
            raise ValueError("monitor planes must be ordered in propagation direction")
        frequencies = [sample.frequency_hz for sample in self.samples]
        if frequencies != sorted(set(frequencies)):
            raise ValueError("frequencies must be unique and sorted")
        return self


def extract_profile_ratio(
    frequencies_hz: tuple[float, ...],
    upstream: NDArray[np.complex128],
    downstream: NDArray[np.complex128],
    *,
    upstream_plane_m: float,
    downstream_plane_m: float,
    field_component: Literal["Ez", "Hz"],
    output_channel: str,
    excitation_channel: str,
    grid_spacing_m: float,
    time_step_s: float,
    steps: int,
    maximum_profile_residual: float = 0.1,
    raw_artifacts: tuple[str, ...] = (),
) -> Result[WaveguideEvidence]:
    """Least-squares downstream/upstream profile ratio on identical y grids.

    Profiles must have the same transverse coordinates, physical orientation,
    weights, and component. The adapter must supply actual sampled planes.
    Finite equal-grid profiles are required; zero or dissimilar profiles produce
    actionable diagnostics. This operation alone never qualifies a modal model.
    """

    def fail(code: str, message: str) -> Result[WaveguideEvidence]:
        return Result(
            None, (Diagnostic(severity=Severity.ERROR, code=code, message=message),)
        )

    if (
        upstream.ndim != 2
        or upstream.shape != downstream.shape
        or upstream.shape[0] != len(frequencies_hz)
    ):
        return fail(
            "waveguide.profile_shape",
            "profiles require matching (frequency, transverse coordinate) arrays",
        )
    if not np.all(np.isfinite(upstream)) or not np.all(np.isfinite(downstream)):
        return fail("waveguide.nonfinite", "profiles contain nonfinite samples")
    samples: list[TransferSample] = []
    for frequency, first, second in zip(
        frequencies_hz, upstream, downstream, strict=True
    ):
        energy = float(np.vdot(first, first).real)
        second_norm = float(np.linalg.norm(second))
        if energy <= 0 or second_norm <= 0:
            return fail(
                "waveguide.zero_profile",
                "both monitor profiles require nonzero coherent signal",
            )
        ratio = complex(np.vdot(first, second) / energy)
        residual = float(np.linalg.norm(second - ratio * first) / second_norm)
        if residual > maximum_profile_residual:
            return fail(
                "waveguide.profile_mismatch",
                f"profile residual {residual:g} exceeds {maximum_profile_residual:g} at {frequency:g} Hz",
            )
        if not cmath.isfinite(ratio) or abs(ratio) == 0:
            return fail(
                "waveguide.invalid_ratio", "coherent transfer is zero or nonfinite"
            )
        samples.append(
            TransferSample(
                frequency_hz=frequency,
                real=ratio.real,
                imag=ratio.imag,
                profile_residual=residual,
            )
        )
    return Result.of(
        WaveguideEvidence(
            upstream_plane_m=upstream_plane_m,
            downstream_plane_m=downstream_plane_m,
            field_component=field_component,
            output_channel=output_channel,
            excitation_channel=excitation_channel,
            grid_spacing_m=grid_spacing_m,
            time_step_s=time_step_s,
            steps=steps,
            samples=tuple(samples),
            raw_artifacts=raw_artifacts,
        )
    )
