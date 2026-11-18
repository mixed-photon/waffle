"""Magnitude and wrapped phase checks kept separate from convergence."""

from __future__ import annotations

import cmath
from typing import Literal

from waffle.contracts.characterization.report import Comparison


def compare_complex(
    observed: complex,
    reference: complex,
    frequency_hz: float,
    *,
    magnitude_tolerance: float,
    phase_tolerance_rad: float,
    comparison: Literal[
        "analytic_reference",
        "grid_convergence",
        "time_convergence",
        "boundary_convergence",
        "held_out",
    ],
) -> tuple[Comparison, Comparison]:
    """Absolute amplitude difference and principal phase difference in radians.

    Phase is undefined for zero amplitude: callers must diagnose that case rather
    than accepting a synthetic zero-phase comparison.
    """
    if not all(cmath.isfinite(value) for value in (observed, reference)):
        raise ValueError("comparison requires finite complex amplitudes")
    if abs(observed) == 0 or abs(reference) == 0:
        raise ValueError("phase comparison requires nonzero amplitudes")
    common = {"frequency_hz": frequency_hz, "comparison": comparison}
    return (
        Comparison(
            quantity="magnitude",
            error=abs(abs(observed) - abs(reference)),
            tolerance=magnitude_tolerance,
            unit="1",
            **common,
        ),
        Comparison(
            quantity="wrapped_phase",
            error=abs(cmath.phase(observed / reference)),
            tolerance=phase_tolerance_rad,
            unit="rad",
            **common,
        ),
    )
