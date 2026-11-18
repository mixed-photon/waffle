from __future__ import annotations

import pytest

from waffle.contracts.foundation.diagnostics import Severity
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.technology.materials import ConstantIndex, Material
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.photonics.device_analysis.reduction import (
    METHOD,
    Window,
    reduce_column,
    reduce_stack,
)
from waffle.simulation.photonics.device_analysis.slab_reference import (
    symmetric_transcendental,
)
from waffle.technology.materials import refractive_index

WAVELENGTH_PM = 1_550_000
# 1 um of cladding below and above the 0 to 220 nm of patterned levels.
WINDOW = Window(-1_000_000, 1_220_000)
TE, TM = Polarization.TE, Polarization.TM


def index(tech: TechnologyRevision, name: str) -> float:
    material = tech.material(name)
    assert material is not None
    return refractive_index(material, Quantity(value=1.55, unit="um")).unwrap().real


def test_core_column_is_the_symmetric_slab_mode() -> None:
    tech = synthetic_soi()
    column = reduce_column(tech, "WG", WINDOW, WAVELENGTH_PM, TE).unwrap()
    assert [(s.z_min_pm, s.z_max_pm, s.material) for s in column.segments] == [
        (-1_000_000, 0, "sio2"),
        (0, 220_000, "si"),
        (220_000, 1_220_000, "sio2"),
    ]
    assert column.rule == "guided_mode" and column.layer == "WG"
    reference = symmetric_transcendental(
        index(tech, "si"), index(tech, "sio2"), 220e-9, 1.55e-6, TE
    )
    assert column.n_eff == pytest.approx(reference, abs=1e-9)
    tm = reduce_column(tech, "WG", WINDOW, WAVELENGTH_PM, TM).unwrap()
    assert tm.n_eff == pytest.approx(
        symmetric_transcendental(index(tech, "si"), index(tech, "sio2"), 220e-9, 1.55e-6, TM),
        abs=1e-9,
    )


def test_slab_column_guides_a_thinner_mode() -> None:
    tech = synthetic_soi()
    slab = reduce_column(tech, "SLAB", WINDOW, WAVELENGTH_PM, TE).unwrap()
    core = reduce_column(tech, "WG", WINDOW, WAVELENGTH_PM, TE).unwrap()
    assert [(s.z_min_pm, s.z_max_pm) for s in slab.segments][1] == (0, 90_000)
    assert slab.n_eff == pytest.approx(
        symmetric_transcendental(index(tech, "si"), index(tech, "sio2"), 90e-9, 1.55e-6, TE),
        abs=1e-9,
    )
    assert index(tech, "sio2") < slab.n_eff < core.n_eff


def test_background_is_the_cladding_index_by_the_documented_rule() -> None:
    tech = synthetic_soi()
    result = reduce_column(tech, None, WINDOW, WAVELENGTH_PM, TE)
    background = result.unwrap()
    assert background.layer is None and background.rule == "cladding_cutoff"
    assert [(s.z_min_pm, s.z_max_pm, s.material) for s in background.segments] == [
        (-1_000_000, 1_220_000, "sio2")
    ]
    assert background.n_eff == pytest.approx(index(tech, "sio2"), abs=1e-11)
    assert result.diagnostics == ()  # an unguided background is expected, not a warning


def test_window_beyond_the_stack_is_padded_with_the_background() -> None:
    tech = synthetic_soi()
    window = Window(-1_000_000, 5_000_000)  # the stack ends at 2.22 um
    column = reduce_column(tech, "WG", window, WAVELENGTH_PM, TE).unwrap()
    assert [(s.z_min_pm, s.z_max_pm, s.material) for s in column.segments][-1] == (
        220_000,
        5_000_000,
        "sio2",
    )


def test_a_window_that_reaches_the_substrate_removes_the_guided_mode_and_warns() -> None:
    result = reduce_column(synthetic_soi(), "WG", Window(-3_000_000, 1_220_000), WAVELENGTH_PM, TE)
    column = result.unwrap()
    assert column.rule == "cladding_cutoff" and column.n_eff == 3.476
    assert [(d.severity, d.code) for d in result.diagnostics] == [
        (Severity.WARNING, "physics.reduction.no_guided_mode")
    ]


def test_reduction_collects_background_then_layers_and_records_the_method() -> None:
    reduction = reduce_stack(synthetic_soi(), ["SLAB", "WG"], WINDOW, WAVELENGTH_PM, TM, 0).unwrap()
    assert [c.layer for c in reduction.columns] == [None, "SLAB", "WG"]
    assert reduction.method == METHOD and reduction.method_version == 1
    assert (reduction.polarization, reduction.mode_order, reduction.wavelength_pm) == (
        TM,
        0,
        WAVELENGTH_PM,
    )
    assert (reduction.window_z_min_pm, reduction.window_z_max_pm) == (-1_000_000, 1_220_000)
    assert "wavelength" in reduction.validity


def test_absorbing_materials_are_unsupported() -> None:
    tech = synthetic_soi()
    lossy = tuple(
        Material(
            name=m.name,
            optical=ConstantIndex(n=3.476, k=0.01),
            validity=m.validity,
            source=m.source,
        )
        if m.name == "si"
        else m
        for m in tech.materials
    )
    result = reduce_stack(
        tech.model_copy(update={"materials": lossy}), ["WG"], WINDOW, 1_550_000, TE
    )
    assert [d.code for d in result.diagnostics] == ["physics.reduction.absorbing_material"]
    assert result.value is None


def test_material_out_of_range_is_reported_not_extrapolated() -> None:
    result = reduce_stack(synthetic_soi(), ["WG"], WINDOW, 2_000_000, TE)
    assert "technology.material_out_of_range" in {d.code for d in result.diagnostics}
    assert result.value is None


def test_unknown_material_is_reported() -> None:
    tech = synthetic_soi()
    broken = tech.model_copy(
        update={"materials": tuple(m for m in tech.materials if m.name != "si")}
    )
    result = reduce_column(broken, "WG", WINDOW, WAVELENGTH_PM, TE)
    assert [d.code for d in result.diagnostics] == ["physics.reduction.material_unknown"]


def test_unknown_layer_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="NOPE"):
        reduce_column(synthetic_soi(), "NOPE", WINDOW, WAVELENGTH_PM, TE)
