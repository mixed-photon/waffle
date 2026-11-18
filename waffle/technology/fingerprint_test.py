from __future__ import annotations

from typing import Any

from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import ConstantIndex, WavelengthRange
from waffle.contracts.technology.revision import Patterned, TechnologyRevision
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.technology.fingerprint import physical_fingerprint


def edit_level(tech: TechnologyRevision, name: str, **changes: Any) -> TechnologyRevision:
    levels = tuple(
        lv.model_copy(update=changes) if lv.name == name else lv for lv in tech.stack.levels
    )
    return tech.model_copy(update={"stack": tech.stack.model_copy(update={"levels": levels})})


def edit_material(tech: TechnologyRevision, index: int, **changes: Any) -> TechnologyRevision:
    materials = list(tech.materials)
    materials[index] = materials[index].model_copy(update=changes)
    return tech.model_copy(update={"materials": tuple(materials)})


def test_fingerprint_is_stable_and_prefixed() -> None:
    assert physical_fingerprint(synthetic_soi()) == physical_fingerprint(synthetic_soi())
    assert physical_fingerprint(synthetic_soi()).startswith("sha256:")


def test_descriptive_edits_change_the_revision_but_not_the_fingerprint() -> None:
    tech = synthetic_soi()
    layers = (tech.layers[0].model_copy(update={"description": "reworded"}), *tech.layers[1:])
    edited = [
        tech.model_copy(update={"metadata": {"notice": "reworded", "extra": "x"}}),
        tech.model_copy(update={"layers": layers}),
        edit_material(tech, 0, source="a different citation"),
        tech.model_copy(update={"id": tech.id.__class__("technology", "renamed")}),
    ]
    for other in edited:
        assert revision_of(other) != revision_of(tech)
        assert physical_fingerprint(other) == physical_fingerprint(tech)


def test_process_changes_change_revision_and_fingerprint() -> None:
    tech = synthetic_soi()
    thicker = edit_level(tech, "core", thickness=Quantity(value=140, unit="nm"))
    rebased = edit_level(tech, "box", z_min=Quantity(value=-3, unit="um"))
    other_material = edit_material(tech, 0, optical=ConstantIndex(n=3.5))
    other_validity = edit_material(
        tech,
        0,
        validity=WavelengthRange(
            minimum=Quantity(value=1.3, unit="um"), maximum=Quantity(value=1.7, unit="um")
        ),
    )
    remapped = edit_level(tech, "core", pattern=Patterned(layers=("SLAB",), fill="sio2"))
    tilted = edit_level(tech, "core", sidewall_angle=Quantity(value=85, unit="deg"))
    new_pair = tech.model_copy(
        update={"layers": (tech.layers[0].model_copy(update={"gds_datatype": 7}), *tech.layers[1:])}
    )
    new_grid = tech.model_copy(update={"dbu_pm": 500})
    fingerprints = {
        physical_fingerprint(t)
        for t in (
            tech,
            thicker,
            rebased,
            other_material,
            other_validity,
            remapped,
            tilted,
            new_pair,
            new_grid,
        )
    }
    assert len(fingerprints) == 9
    for changed in (thicker, other_material, remapped):
        assert revision_of(changed) != revision_of(tech)


def test_equivalent_units_and_orderings_give_the_same_fingerprint() -> None:
    tech = synthetic_soi()
    in_um = edit_level(
        tech,
        "core",
        z_min=Quantity(value=0.09, unit="um"),
        thickness=Quantity(value=0.13, unit="um"),
    )
    assert revision_of(in_um) != revision_of(tech)
    assert physical_fingerprint(in_um) == physical_fingerprint(tech)
    shuffled = tech.model_copy(
        update={
            "layers": tuple(reversed(tech.layers)),
            "materials": tuple(reversed(tech.materials)),
        }
    )
    assert physical_fingerprint(shuffled) == physical_fingerprint(tech)
    swapped = edit_level(tech, "slab", pattern=Patterned(layers=("SLAB", "WG"), fill="sio2"))
    assert physical_fingerprint(swapped) == physical_fingerprint(tech)
