from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.foundation.units import Quantity
from waffle.contracts.models.capability import (
    Applicability,
    AxisCoverage,
    NumericInterval,
    Uncertainty,
)


def test_sampled_and_validated_coverage_are_independent() -> None:
    axis = AxisCoverage(
        parameter="frequency", sampled=(Quantity(value=190, unit="THz"),)
    )
    assert axis.validated is None
    assert axis.sampled is not None
    assert Applicability(axes=(axis,)).uncertainty.status == "unknown"


def test_bounds_convert_units_and_reject_invalid_claims() -> None:
    band = NumericInterval(
        minimum=Quantity(value=190, unit="THz"), maximum=Quantity(value=200, unit="THz")
    )
    assert band.contains(Quantity(value=195_000_000, unit="MHz"))
    with pytest.raises(ValidationError):
        NumericInterval(
            minimum=Quantity(value=2, unit="um"), maximum=Quantity(value=3, unit="THz")
        )
    with pytest.raises(ValidationError):
        Uncertainty(status="unknown", absolute_bound=Quantity(value=0, unit="1"))
