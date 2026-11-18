from __future__ import annotations

from typing import Any

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    Sellmeier,
    Tabulated,
    WavelengthRange,
)
from waffle.contracts.technology.revision import (
    LayerPurpose,
    LayoutLayer,
    Patterned,
    TechnologyRevision,
)
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.technology.validate import validate_technology


def edit(tech: TechnologyRevision, **changes: Any) -> TechnologyRevision:
    """A copy with fields replaced (no validation, so tests can build broken records)."""
    return tech.model_copy(update=changes)


def edit_level(tech: TechnologyRevision, name: str, **changes: Any) -> TechnologyRevision:
    levels = tuple(
        lv.model_copy(update=changes) if lv.name == name else lv for lv in tech.stack.levels
    )
    return edit(tech, stack=tech.stack.model_copy(update={"levels": levels}))


def codes(diagnostics: list[Diagnostic]) -> list[str]:
    return [d.code for d in diagnostics]


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def test_synthetic_technology_is_valid() -> None:
    assert validate_technology(synthetic_soi()) == []


def test_grid_must_be_a_multiple_of_the_database_unit() -> None:
    found = validate_technology(edit(synthetic_soi(), manufacturing_grid_pm=1500))
    assert codes(found) == ["technology.grid_mismatch"]
    assert found[0].location == ("manufacturing_grid_pm",)
    assert validate_technology(edit(synthetic_soi(), manufacturing_grid_pm=None)) == []


def test_optical_band_must_be_a_valid_range() -> None:
    band = WavelengthRange(minimum=um(1.6), maximum=um(1.5))
    found = validate_technology(edit(synthetic_soi(), optical_band=band))
    assert codes(found) == ["technology.wavelength_range_invalid"]
    assert found[0].location == ("optical_band",)


def test_duplicate_layer_names_and_gds_pairs() -> None:
    tech = synthetic_soi()
    wg = tech.layers[0]
    dup_name = wg.model_copy(update={"gds_layer": 77})
    dup_pair = wg.model_copy(update={"name": "OTHER"})
    found = validate_technology(edit(tech, layers=(*tech.layers, dup_name, dup_pair)))
    assert sorted(codes(found)) == [
        "technology.duplicate_gds_pair",
        "technology.duplicate_layer_name",
        "technology.layer_unmapped",  # OTHER patterns nothing
    ]
    by_code = {d.code: d for d in found}
    assert by_code["technology.duplicate_layer_name"].location == ("layers", 4, "name")
    assert by_code["technology.duplicate_gds_pair"].location == ("layers", 5)
    assert "'WG'" in by_code["technology.duplicate_gds_pair"].message


def test_duplicate_materials_and_levels() -> None:
    tech = synthetic_soi()
    found = validate_technology(
        edit(tech, materials=(*tech.materials, tech.materials[0])),
    )
    assert codes(found) == ["technology.duplicate_material"]
    levels = (*tech.stack.levels, tech.stack.levels[-1].model_copy(update={"z_min": um(5)}))
    found = validate_technology(edit(tech, stack=tech.stack.model_copy(update={"levels": levels})))
    assert codes(found) == ["technology.duplicate_level"]


def test_unknown_materials_are_located() -> None:
    tech = edit_level(synthetic_soi(), "box", material="oxide")
    tech = edit_level(tech, "core", pattern=Patterned(layers=("WG",), fill="air"))
    tech = edit(tech, stack=tech.stack.model_copy(update={"background": "vacuum"}))
    found = validate_technology(tech)
    assert codes(found) == ["technology.unknown_material"] * 3
    assert sorted(d.location for d in found) == [
        ("stack", "background"),
        ("stack", "levels", 1, "material"),
        ("stack", "levels", 3, "pattern", "fill"),
    ]


def test_layers_used_by_the_stack_must_exist_and_must_not_be_annotations() -> None:
    tech = edit_level(
        synthetic_soi(), "core", pattern=Patterned(layers=("WG", "NOPE", "PORT"), fill="sio2")
    )
    found = validate_technology(tech)
    assert codes(found) == [
        "technology.unknown_layer",
        "technology.annotation_layer_patterns_stack",
    ]
    assert found[0].location == ("stack", "levels", 3, "pattern", "layers", 1)
    assert found[1].location == ("stack", "levels", 3, "pattern", "layers", 2)


def test_unpatterned_drawing_layer_is_a_warning() -> None:
    tech = synthetic_soi()
    extra = LayoutLayer(name="HEATER", gds_layer=10, gds_datatype=0)
    found = validate_technology(edit(tech, layers=(*tech.layers, extra)))
    assert codes(found) == ["technology.layer_unmapped"]
    assert found[0].severity is Severity.WARNING
    annotation = extra.model_copy(update={"purpose": LayerPurpose.ANNOTATION})
    assert validate_technology(edit(tech, layers=(*tech.layers, annotation))) == []


def test_thickness_must_be_positive() -> None:
    for thickness in (0, -10):
        tech = edit_level(synthetic_soi(), "core", thickness=Quantity(value=thickness, unit="nm"))
        found = validate_technology(tech)
        assert codes(found) == ["technology.nonpositive_thickness"]
        assert found[0].location == ("stack", "levels", 3, "thickness")


def test_unknown_and_wrong_dimension_units_are_reported() -> None:
    tech = edit_level(synthetic_soi(), "core", thickness=Quantity(value=130, unit="furlong"))
    tech = edit_level(tech, "slab", z_min=Quantity(value=0, unit="ps"))
    found = validate_technology(tech)
    assert codes(found) == ["units.dimension_mismatch", "units.unknown_unit"]
    assert found[0].location == ("stack", "levels", 2, "z_min")


def test_overlapping_levels_are_an_error_but_touching_levels_are_not() -> None:
    assert validate_technology(synthetic_soi()) == []  # slab top == core bottom
    tech = edit_level(synthetic_soi(), "core", z_min=Quantity(value=80, unit="nm"))
    found = validate_technology(tech)
    assert codes(found) == ["technology.level_overlap"]
    assert found[0].location == ("stack", "levels", 3)


def test_level_order_is_enforced() -> None:
    tech = synthetic_soi()
    levels = (tech.stack.levels[1], tech.stack.levels[0], *tech.stack.levels[2:])
    found = validate_technology(edit(tech, stack=tech.stack.model_copy(update={"levels": levels})))
    assert codes(found) == ["technology.level_order"]


def test_gaps_between_levels_are_allowed_and_hold_the_background() -> None:
    tech = edit_level(synthetic_soi(), "cladding", z_min=um(1))
    assert validate_technology(tech) == []


def test_sidewall_angle_rules() -> None:
    def angle(value: float, unit: str = "deg") -> Quantity:
        return Quantity(value=value, unit=unit)

    ok = edit_level(synthetic_soi(), "core", sidewall_angle=angle(85))
    assert validate_technology(ok) == []
    assert validate_technology(edit_level(ok, "core", sidewall_angle=angle(90))) == []
    for bad in (angle(0), angle(95), angle(-3), angle(2, "rad")):
        found = validate_technology(edit_level(ok, "core", sidewall_angle=bad))
        assert codes(found) == ["technology.sidewall_angle_invalid"], bad
    found = validate_technology(edit_level(ok, "core", sidewall_angle=angle(1, "um")))
    assert codes(found) == ["units.dimension_mismatch"]
    found = validate_technology(edit_level(ok, "box", sidewall_angle=angle(85)))
    assert codes(found) == ["technology.sidewall_on_blanket"]


def test_material_must_cover_the_optical_band_where_used() -> None:
    tech = synthetic_soi()
    narrow = tech.materials[0].model_copy(
        update={"validity": WavelengthRange(minimum=um(1.52), maximum=um(1.58))}
    )
    found = validate_technology(edit(tech, materials=(narrow, tech.materials[1])))
    assert codes(found) == ["technology.material_validity_gap"]
    assert found[0].location == ("materials", 0, "validity")
    # An unused material may have any validity.
    unused = Material(
        name="spare", optical=ConstantIndex(n=1.0), validity=narrow.validity, source="test"
    )
    assert validate_technology(edit(tech, materials=(*tech.materials, unused))) == []


def test_material_validity_range_itself_is_checked() -> None:
    tech = synthetic_soi()
    bad = tech.materials[0].model_copy(
        update={"validity": WavelengthRange(minimum=um(2), maximum=um(1))}
    )
    found = validate_technology(edit(tech, materials=(bad, tech.materials[1])))
    assert codes(found) == ["technology.wavelength_range_invalid"]
    assert found[0].location == ("materials", 0, "validity")


def test_sellmeier_pole_inside_validity_is_an_error() -> None:
    tech = synthetic_soi()
    pole = tech.materials[1].model_copy(update={"optical": Sellmeier(b=(1.0,), c_um2=(1.55**2,))})
    found = validate_technology(edit(tech, materials=(tech.materials[0], pole)))
    assert codes(found) == ["technology.material_pole_in_range"]


def test_tabulated_validity_must_lie_inside_the_table() -> None:
    tech = synthetic_soi()
    table = Tabulated(wavelength_um=(1.5, 1.6), n=(3.4, 3.5), k=(0.0, 0.0))
    material = tech.materials[0].model_copy(update={"optical": table})
    found = validate_technology(edit(tech, materials=(material, tech.materials[1])))
    assert codes(found) == ["technology.material_validity_exceeds_table"]
