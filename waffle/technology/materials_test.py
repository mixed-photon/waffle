from __future__ import annotations

import pytest

from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    Sellmeier,
    Tabulated,
    WavelengthRange,
)
from waffle.technology.materials import refractive_index


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def material(optical, low: float = 0.2, high: float = 4.0) -> Material:  # type: ignore[no-untyped-def]
    return Material(
        name="m",
        optical=optical,
        validity=WavelengthRange(minimum=um(low), maximum=um(high)),
        source="test",
    )


# Malitson, J. Opt. Soc. Am. 55, 1205 (1965), fused silica at 20 degC: n = 1.4440 at
# 1.55 um and n = 1.4585 at the sodium D line (0.5893 um).
SILICA = material(
    Sellmeier(
        b=(0.6961663, 0.4079426, 0.8974794),
        c_um2=(0.0684043**2, 0.1162414**2, 9.896161**2),
    ),
    0.21,
    3.71,
)


def test_fused_silica_matches_published_index() -> None:
    assert refractive_index(SILICA, um(1.55)).unwrap() == pytest.approx(1.4440, abs=1e-4)
    assert refractive_index(SILICA, um(0.5893)).unwrap() == pytest.approx(1.4585, abs=1e-4)
    assert refractive_index(SILICA, um(1.55)).unwrap().imag == 0.0


def test_wavelength_units_are_converted() -> None:
    nm = Quantity(value=1550, unit="nm")
    assert refractive_index(SILICA, nm).unwrap() == pytest.approx(
        refractive_index(SILICA, um(1.55)).unwrap()
    )


def test_constant_index_is_complex() -> None:
    value = refractive_index(material(ConstantIndex(n=3.476, k=0.001)), um(1.55)).unwrap()
    assert value == complex(3.476, 0.001)


def test_tabulated_interpolates_linearly_inside_the_table() -> None:
    table = material(Tabulated(wavelength_um=(1.5, 1.6), n=(3.0, 4.0), k=(0.0, 0.2)), 1.5, 1.6)
    value = refractive_index(table, um(1.55)).unwrap()
    assert value.real == pytest.approx(3.5)
    assert value.imag == pytest.approx(0.1)
    assert refractive_index(table, um(1.5)).unwrap() == complex(3.0, 0.0)


@pytest.mark.parametrize("wavelength", [0.5, 2.5, 1e-3])
def test_outside_validity_is_a_diagnostic_not_a_number(wavelength: float) -> None:
    result = refractive_index(material(ConstantIndex(n=2.0), 1.0, 2.0), um(wavelength))
    assert result.value is None
    assert [d.code for d in result.diagnostics] == ["technology.material_out_of_range"]
    assert "1 to 2 um" in result.diagnostics[0].message


def test_tabulated_is_not_extrapolated_even_if_validity_claims_it() -> None:
    table = Tabulated(wavelength_um=(1.5, 1.6), n=(3.0, 4.0), k=(0.0, 0.0))
    result = refractive_index(material(table, 1.0, 2.0), um(1.7))
    assert [d.code for d in result.diagnostics] == ["technology.material_out_of_range"]


def test_bad_wavelength_unit_is_reported() -> None:
    result = refractive_index(SILICA, Quantity(value=1.55, unit="ps"))
    assert [d.code for d in result.diagnostics] == ["units.dimension_mismatch"]
    assert result.diagnostics[0].location == ("wavelength",)


def test_bad_validity_range_is_reported() -> None:
    backwards = material(ConstantIndex(n=2.0), 2.0, 1.0)
    assert [d.code for d in refractive_index(backwards, um(1.5)).diagnostics] == [
        "technology.wavelength_range_invalid"
    ]


def test_nonphysical_sellmeier_is_reported() -> None:
    # n^2 = 1 + 5 L^2 / (L^2 - 4) is about -0.67 at 1 um, below the resonance at 2 um.
    odd = material(Sellmeier(b=(5.0,), c_um2=(4.0,)), 0.5, 1.5)
    result = refractive_index(odd, um(1.0))
    assert [d.code for d in result.diagnostics] == ["technology.material_nonphysical"]
