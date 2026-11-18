from __future__ import annotations

import cmath

import pytest

from waffle.characterization.validation.compare import compare_complex


def test_wrapped_phase_and_magnitude() -> None:
    result = compare_complex(
        cmath.exp(3.13j),
        cmath.exp(-3.13j),
        2e14,
        magnitude_tolerance=1e-10,
        phase_tolerance_rad=0.03,
        comparison="grid_convergence",
    )
    assert all(item.passed for item in result)
    assert result[1].error == pytest.approx(2 * (cmath.pi - 3.13))


def test_zero_phase_is_not_computed() -> None:
    with pytest.raises(ValueError, match="nonzero"):
        compare_complex(
            0j,
            1j,
            2e14,
            magnitude_tolerance=1,
            phase_tolerance_rad=1,
            comparison="analytic_reference",
        )
