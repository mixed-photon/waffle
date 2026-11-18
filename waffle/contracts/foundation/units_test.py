from __future__ import annotations

import pytest

from waffle.contracts.foundation.units import UNITS, Dimension, Quantity


def test_conversion_between_units_of_one_dimension() -> None:
    assert Quantity(value=1550.0, unit="nm").to("um").value == pytest.approx(1.55)
    assert Quantity(value=193.4, unit="THz").to("GHz").value == pytest.approx(193400.0)
    assert Quantity(value=180.0, unit="deg").to("rad").value == pytest.approx(3.141592653589793)


def test_degc_is_an_offset_unit() -> None:
    assert Quantity(value=25.0, unit="degC").si == pytest.approx(298.15)
    assert Quantity(value=300.0, unit="K").to("degC").value == pytest.approx(26.85)


def test_conversion_across_dimensions_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot convert"):
        Quantity(value=1.0, unit="nm").to("ps")
    with pytest.raises(ValueError, match="unknown unit"):
        _ = Quantity(value=1.0, unit="furlong").si


def test_diagnostics_for_unknown_unit_and_wrong_dimension() -> None:
    ok = Quantity(value=1.0, unit="um").diagnostics(Dimension.LENGTH)
    assert ok == []
    [unknown] = Quantity(value=1.0, unit="furlong").diagnostics(
        Dimension.LENGTH, subject="x", location=("w",)
    )
    assert unknown.code == "units.unknown_unit"
    assert unknown.location == ("w",)
    [mismatch] = Quantity(value=1.0, unit="ps").diagnostics(Dimension.LENGTH)
    assert mismatch.code == "units.dimension_mismatch"


def test_quantity_json_shape_and_round_trip() -> None:
    q = Quantity(value=0.5, unit="um")
    assert q.model_dump(mode="json") == {"value": 0.5, "unit": "um"}
    assert Quantity.model_validate_json(q.model_dump_json()) == q


def test_non_finite_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        Quantity(value=float("nan"), unit="m")


def test_every_dimension_has_a_unit() -> None:
    assert {u.dimension for u in UNITS.values()} == set(Dimension)
