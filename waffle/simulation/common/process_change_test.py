"""MOD-006 criterion 5 and MOD-018 inputs: which changes alter the resolved problem."""

from __future__ import annotations

import pytest

from waffle.contracts.foundation.records import revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.shapes import Rectangle
from waffle.contracts.layout.layout import Layout
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.technology.materials import ConstantIndex, Material
from waffle.contracts.technology.revision import Patterned, TechnologyRevision
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common import examples
from waffle.simulation.common.examples import request
from waffle.simulation.common.fingerprint import physical_problem_fingerprint
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.common.request import EffectiveIndexSpec, Margins

TECH = synthetic_soi()
DIMENSIONS = ["3d", "2d"]


def prepare(
    tech: TechnologyRevision = TECH,
    layout: Layout | None = None,
    dimensionality: str = "3d",
    **overrides: object,
) -> PhysicalProblem:
    return prepare_physical_problem(
        layout or examples.straight_waveguide(), tech, request(dimensionality, **overrides)
    ).unwrap()


def with_level(tech: TechnologyRevision, name: str, **update: object) -> TechnologyRevision:
    levels = tuple(
        lv.model_copy(update=update) if lv.name == name else lv for lv in tech.stack.levels
    )
    return tech.model_copy(update={"stack": tech.stack.model_copy(update={"levels": levels})})


def with_si_index(tech: TechnologyRevision, n: float) -> TechnologyRevision:
    materials = tuple(
        m.model_copy(update={"optical": ConstantIndex(n=n)}) if m.name == "si" else m
        for m in tech.materials
    )
    return tech.model_copy(update={"materials": materials})


def assert_physically_different(a: PhysicalProblem, b: PhysicalProblem) -> None:
    assert revision_of(a) != revision_of(b)
    assert physical_problem_fingerprint(a) != physical_problem_fingerprint(b)


@pytest.mark.parametrize("dimensionality", DIMENSIONS)
def test_level_thickness_changes_the_problem(dimensionality: str) -> None:
    thicker = with_level(
        with_level(TECH, "core", thickness=Quantity(value=140, unit="nm")),
        "cladding",
        z_min=Quantity(value=230, unit="nm"),  # the cladding rises with the core
    )
    assert_physically_different(
        prepare(TECH, dimensionality=dimensionality),
        prepare(thicker, dimensionality=dimensionality),
    )


@pytest.mark.parametrize("dimensionality", DIMENSIONS)
def test_material_index_changes_the_problem(dimensionality: str) -> None:
    assert_physically_different(
        prepare(TECH, dimensionality=dimensionality),
        prepare(with_si_index(TECH, 3.5), dimensionality=dimensionality),
    )


@pytest.mark.parametrize("dimensionality", DIMENSIONS)
def test_layer_to_level_mapping_changes_the_problem(dimensionality: str) -> None:
    remapped = with_level(TECH, "slab", pattern=Patterned(layers=("SLAB",), fill="sio2"))
    assert_physically_different(
        prepare(TECH, dimensionality=dimensionality),
        prepare(remapped, dimensionality=dimensionality),
    )


def test_2d_reduction_settings_change_the_problem() -> None:
    def reduction(um: float, polarization: Polarization, order: int = 0) -> PhysicalProblem:
        spec = EffectiveIndexSpec(
            wavelength=Quantity(value=um, unit="um"), polarization=polarization, mode_order=order
        )
        return prepare(dimensionality="2d", reduction=spec)

    base = reduction(1.55, Polarization.TE)
    others = [
        reduction(1.5, Polarization.TE),
        reduction(1.55, Polarization.TM),
        reduction(1.55, Polarization.TE, order=1),
    ]
    for other in others:
        assert_physically_different(base, other)
    assert len({physical_problem_fingerprint(p) for p in [base, *others]}) == 4


def test_geometry_margins_and_dimensionality_change_the_problem() -> None:
    base = prepare()
    narrower = Rectangle(lo=(0, -225), hi=(10000, 225))
    layout = examples.straight_waveguide()
    cell = layout.cells[0].model_copy(update={"shapes": {"WG": (narrower,)}})
    assert_physically_different(
        base, prepare(layout=layout.model_copy(update={"cells": (cell,)}), ports=())
    )
    wider_domain = Margins(
        lateral=Quantity(value=3, unit="um"), vertical=Quantity(value=1, unit="um")
    )
    assert_physically_different(base, prepare(margins=wider_domain))
    assert_physically_different(base, prepare(dimensionality="2d"))


def test_descriptive_technology_edits_change_the_reference_but_not_the_physics() -> None:
    described = TECH.model_copy(
        update={
            "metadata": {"notice": "relabelled"},
            "layers": tuple(
                layer.model_copy(update={"description": "other words"}) for layer in TECH.layers
            ),
            "materials": tuple(
                m.model_copy(update={"source": "another citation"}) for m in TECH.materials
            ),
        }
    )
    assert revision_of(described) != revision_of(TECH)
    for dimensionality in DIMENSIONS:
        before = prepare(TECH, dimensionality=dimensionality)
        after = prepare(described, dimensionality=dimensionality)
        assert after.technology != before.technology
        assert revision_of(after) != revision_of(before)
        if dimensionality == "2d":
            assert after.description == before.description
        assert after.approximations == before.approximations
        assert physical_problem_fingerprint(after) == physical_problem_fingerprint(before)


def test_descriptive_problem_edits_do_not_change_the_fingerprint() -> None:
    base = prepare()
    renamed = prepare(name="other")
    described = prepare(metadata={"note": "free text"})
    for other in (renamed, described):
        assert revision_of(other) != revision_of(base)
        assert physical_problem_fingerprint(other) == physical_problem_fingerprint(base)


def test_changes_outside_the_domain_are_not_physical() -> None:
    base = prepare()
    thinner_substrate = with_level(TECH, "substrate", thickness=Quantity(value=500, unit="um"))
    other = prepare(thinner_substrate)
    assert revision_of(other) != revision_of(base)
    assert physical_problem_fingerprint(other) == physical_problem_fingerprint(base)
    assert (
        prepare(thinner_substrate, dimensionality="2d").description
        == prepare(dimensionality="2d").description
    )


def test_preparation_is_deterministic() -> None:
    for dimensionality in DIMENSIONS:
        runs = [
            prepare(layout=examples.hierarchical_bend(), dimensionality=dimensionality)
            for _ in range(3)
        ]
        assert len({revision_of(p) for p in runs}) == 1
        assert len({to_canonical_json(p) for p in runs}) == 1
        assert len({physical_problem_fingerprint(p) for p in runs}) == 1


def test_fingerprint_excludes_source_references_and_includes_port_identity() -> None:
    problem = prepare()
    other_layout = examples.straight_waveguide().model_copy(
        update={"metadata": {"comment": "new revision, same shapes"}}
    )
    same_shapes = prepare(layout=other_layout)
    assert same_shapes.layout != problem.layout
    assert physical_problem_fingerprint(same_shapes) == physical_problem_fingerprint(problem)
    fewer_ports = prepare(ports=("o1",))
    assert physical_problem_fingerprint(fewer_ports) != physical_problem_fingerprint(problem)


def test_material_models_are_copied_into_a_3d_problem() -> None:
    problem = prepare(with_si_index(TECH, 3.5))
    materials = {m.name: m for m in problem.description.materials}  # type: ignore[union-attr]
    assert materials["si"] == Material(
        name="si",
        optical=ConstantIndex(n=3.5),
        validity=materials["si"].validity,
        source=materials["si"].source,
    )
