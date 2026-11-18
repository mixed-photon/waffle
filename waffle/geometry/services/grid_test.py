from __future__ import annotations

from fractions import Fraction

import pytest

from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.geometry.services.grid import Rounding, dbu_to_float, from_dbu, pm_per_unit, to_dbu

NM = Grid(dbu_pm=1000)


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def test_length_units_are_exact_picometres() -> None:
    assert [pm_per_unit(u) for u in ("m", "mm", "um", "nm")] == [10**12, 10**9, 10**6, 10**3]
    with pytest.raises(ValueError):
        pm_per_unit("s")


def test_um_to_dbu_to_um_round_trips_exactly_for_on_grid_values() -> None:
    # 0.1 and 0.3 are not binary-exact floats; the decimal as written must survive.
    for value in (0.0, 0.1, 0.3, 0.001, 0.45, 220.123, 12345.678, -3.14):
        snap = to_dbu(um(value), NM).unwrap()
        assert snap.error_pm == 0
        assert from_dbu(snap.dbu, NM, "um").value == value
    assert to_dbu(um(0.3), NM).unwrap().dbu == 300
    assert to_dbu(Quantity(value=1.5, unit="mm"), NM).unwrap().dbu == 1_500_000
    assert to_dbu(Quantity(value=250, unit="nm"), NM).unwrap().dbu == 250


def test_round_trip_is_exact_on_a_finer_and_a_coarser_grid() -> None:
    fine, coarse = Grid(dbu_pm=1), Grid(dbu_pm=5000)
    assert to_dbu(um(0.000001), fine).unwrap().dbu == 1
    assert to_dbu(um(0.005), coarse).unwrap().dbu == 1
    assert from_dbu(7, coarse, "nm").value == 35000.0 / 1000.0 * 1.0 == 35.0


def test_off_grid_values_are_errors_unless_a_rounding_is_requested() -> None:
    result = to_dbu(um(0.0004), NM, subject="x", location=("origin", 0))
    assert result.value is None
    assert [(d.code, d.location) for d in result.diagnostics] == [
        ("geometry.grid.off_grid", ("origin", 0))
    ]


def test_snapping_reports_its_exact_error() -> None:
    cases = {
        Rounding.NEAREST: (0, Fraction(-400, 1)),
        Rounding.FLOOR: (0, Fraction(-400, 1)),
        Rounding.CEIL: (1, Fraction(600, 1)),
    }
    for rounding, (dbu, error) in cases.items():
        result = to_dbu(um(0.0004), NM, rounding)
        snap = result.unwrap()
        assert (snap.dbu, snap.error_pm) == (dbu, error)
        assert [d.code for d in result.diagnostics] == ["geometry.grid.snapped"]
    # ties go to the even integer
    assert to_dbu(um(0.0005), NM, Rounding.NEAREST).unwrap().dbu == 0
    assert to_dbu(um(0.0015), NM, Rounding.NEAREST).unwrap().dbu == 2


def test_on_grid_values_never_warn_under_any_rounding() -> None:
    for rounding in Rounding:
        assert to_dbu(um(0.2), NM, rounding).diagnostics == ()


def test_non_length_and_unknown_units_are_diagnosed() -> None:
    assert (
        to_dbu(Quantity(value=1.0, unit="s"), NM).diagnostics[0].code == "units.dimension_mismatch"
    )
    assert (
        to_dbu(Quantity(value=1.0, unit="parsec"), NM).diagnostics[0].code == "units.unknown_unit"
    )


def test_dbu_to_float_is_correctly_rounded() -> None:
    assert dbu_to_float(1, Grid(dbu_pm=1000), "um") == 0.001
    assert dbu_to_float(-3, Grid(dbu_pm=500), "um") == -0.0015


def test_grid_validation() -> None:
    with pytest.raises(ValueError):
        Grid(dbu_pm=0)
    with pytest.raises(ValueError):
        Grid(dbu_pm=1000.5)  # type: ignore[arg-type]
    assert Grid(dbu_pm=1000, manufacturing=5).on_manufacturing(10)
    assert not Grid(dbu_pm=1000, manufacturing=5).on_manufacturing(7)
