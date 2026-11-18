from __future__ import annotations

import numpy as np
import pytest

from waffle.simulation.photonics.characterization.waveguide import extract_profile_ratio


def test_profile_ratio_and_mismatch() -> None:
    first = np.array([[1, 2, 1]], dtype=complex)
    kwargs = {
        "upstream_plane_m": 1e-6,
        "downstream_plane_m": 2e-6,
        "field_component": "Hz",
        "output_channel": "o2",
        "excitation_channel": "o1",
        "grid_spacing_m": 1e-8,
        "time_step_s": 1e-17,
        "steps": 100,
    }
    result = extract_profile_ratio((2e14,), first, first * (0.2 + 0.9j), **kwargs)
    assert result.value is not None
    assert result.value.samples[0].amplitude == pytest.approx(0.2 + 0.9j)
    assert result.value.samples[0].profile_residual < 1e-15
    failed = extract_profile_ratio(
        (2e14,), first, np.array([[1, -2, 1]], dtype=complex), **kwargs
    )
    assert failed.value is None
    assert failed.diagnostics[0].code == "waveguide.profile_mismatch"
