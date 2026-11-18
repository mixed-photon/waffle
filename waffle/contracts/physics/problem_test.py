from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.physics.approximations import (
    EffectiveIndexReduction,
    Polarization,
    ReducedColumn,
    StackSegment,
)
from waffle.contracts.physics.problem import (
    Boundaries2D,
    Boundaries3D,
    BoundaryCondition,
    BoundaryKind,
    Domain2D,
    Domain3D,
    Extrusion3D,
    Face,
    Film,
    PhysicalProblem,
    PortPlane,
    Prism,
    Reduced2D,
    Region2D,
    unpaired_periodic,
)
from waffle.contracts.technology.materials import ConstantIndex, Material, WavelengthRange

ABSORB = BoundaryCondition()
SQUARE = Polygon(points=((0, 0), (10, 0), (10, 10), (0, 10)))
LAYOUT = RevisionRef.of(EntityId("layout", "x"), {"a": 1})
TECHNOLOGY = RevisionRef.of(EntityId("technology", "t"), {"a": 1})
PORT = PortPlane(
    path="in/o1", layer="WG", domain=Domain.OPTICAL, center=(0, 5), width=4, orientation=180
)


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def silicon() -> Material:
    return Material(
        name="si",
        optical=ConstantIndex(n=3.5),
        validity=WavelengthRange(minimum=um(1.2), maximum=um(1.7)),
        source="test",
    )


def extrusion(**overrides: Any) -> Extrusion3D:
    fields: dict[str, Any] = {
        "domain": Domain3D(
            lo=(-5, -5),
            hi=(15, 15),
            z_min_pm=-100,
            z_max_pm=300,
            boundaries=Boundaries3D(**{f.value: ABSORB for f in Face}),
        ),
        "materials": (silicon(),),
        "background": "si",
        "films": (Film(level="box", z_min_pm=-100, z_max_pm=0, material="si"),),
        "prisms": (
            Prism(
                level="core", layer="WG", polygon=SQUARE, z_min_pm=0, z_max_pm=200, material="si"
            ),
        ),
        "ports": (),
    }
    return Extrusion3D(**{**fields, **overrides})


def reduction() -> EffectiveIndexReduction:
    segment = StackSegment(z_min_pm=0, z_max_pm=100, material="si", index=2.0)
    return EffectiveIndexReduction(
        method="m",
        method_version=1,
        polarization=Polarization.TE,
        mode_order=0,
        wavelength_pm=1_550_000,
        window_z_min_pm=0,
        window_z_max_pm=100,
        columns=(
            ReducedColumn(layer=None, segments=(segment,), n_eff=1.5, rule="cladding_cutoff"),
            ReducedColumn(layer="WG", segments=(segment,), n_eff=2.5, rule="guided_mode"),
        ),
        validity="one wavelength",
    )


def reduced(**overrides: Any) -> Reduced2D:
    fields: dict[str, Any] = {
        "domain": Domain2D(
            lo=(-5, -5),
            hi=(15, 15),
            boundaries=Boundaries2D(**{f.value: ABSORB for f in Face if f.value[0] != "z"}),
        ),
        "background_index": 1.5,
        "regions": (Region2D(layer="WG", polygons=(SQUARE,), index=2.5),),
        "ports": (PORT,),
    }
    return Reduced2D(**{**fields, **overrides})


def problem(description: Extrusion3D | Reduced2D, *approximations: Any) -> PhysicalProblem:
    return PhysicalProblem(
        id=EntityId("physical_problem", "p"),
        layout=LAYOUT,
        top_cell="top",
        technology=TECHNOLOGY,
        dbu_pm=1000,
        description=description,
        approximations=approximations,
    )


def test_both_dimensionalities_round_trip_with_their_variant() -> None:
    for original, kind in (
        (problem(extrusion()), Extrusion3D),
        (problem(reduced(), reduction()), Reduced2D),
    ):
        loaded = load_record(to_canonical_json(original), PhysicalProblem).unwrap()
        assert loaded == original and isinstance(loaded.description, kind)
        assert revision_of(loaded) == revision_of(original)
        assert loaded.layout == LAYOUT and loaded.technology == TECHNOLOGY


def test_a_description_without_its_dimensionality_tag_is_rejected() -> None:
    data = problem(extrusion()).model_dump(mode="json")
    del data["description"]["dimensionality"]
    assert not load_record(data, PhysicalProblem).ok
    data["description"]["dimensionality"] = "4d"
    assert not load_record(data, PhysicalProblem).ok


def test_2d_needs_exactly_one_reduction_and_3d_none() -> None:
    with pytest.raises(ValidationError, match="exactly one effective-index reduction"):
        problem(reduced())
    with pytest.raises(ValidationError, match="3D description cannot carry"):
        problem(extrusion(), reduction())


def test_2d_indices_must_match_the_reduction() -> None:
    with pytest.raises(ValidationError, match="equal the reduction's"):
        problem(reduced(background_index=1.6), reduction())


def test_port_paths_must_be_unique_and_well_formed() -> None:
    with pytest.raises(ValidationError, match="unique"):
        problem(reduced(ports=(PORT, PORT)), reduction())
    for bad in ("", "/a", "a//b", "a b", "a/"):
        with pytest.raises(ValidationError):
            PortPlane(
                path=bad, layer="WG", domain=Domain.OPTICAL, center=(0, 0), width=1, orientation=0
            )
    assert PORT.path == "in/o1"


def test_3d_materials_must_be_defined() -> None:
    with pytest.raises(ValidationError, match="not defined"):
        extrusion(background="sio2")
    with pytest.raises(ValidationError, match="not defined"):
        extrusion(films=(Film(level="box", z_min_pm=0, z_max_pm=1, material="x"),))


def test_domains_and_spans_must_be_ordered() -> None:
    boundaries = Boundaries2D(x_min=ABSORB, x_max=ABSORB, y_min=ABSORB, y_max=ABSORB)
    with pytest.raises(ValidationError, match="lo < hi"):
        Domain2D(lo=(0, 0), hi=(0, 5), boundaries=boundaries)
    with pytest.raises(ValidationError, match="z_min_pm < z_max_pm"):
        extrusion(
            domain=Domain3D(
                lo=(0, 0),
                hi=(1, 1),
                z_min_pm=5,
                z_max_pm=5,
                boundaries=Boundaries3D(**{f.value: ABSORB for f in Face}),
            )
        )


def test_periodic_faces_come_in_pairs() -> None:
    periodic = BoundaryCondition(kind=BoundaryKind.PERIODIC)
    assert unpaired_periodic({Face.X_MIN: periodic, Face.X_MAX: ABSORB}) == ["x"]
    assert unpaired_periodic({Face.X_MIN: periodic, Face.X_MAX: periodic}) == []
    assert unpaired_periodic({Face.X_MIN: periodic}) == []  # unspecified faces are not compared
    with pytest.raises(ValidationError, match="periodic opposite"):
        Boundaries2D(x_min=periodic, x_max=ABSORB, y_min=ABSORB, y_max=ABSORB)


def test_prism_sidewall_angle_is_between_0_and_90_exclusive() -> None:
    for angle in (0, 90, 120):
        with pytest.raises(ValidationError):
            Prism(
                level="c",
                layer="WG",
                polygon=SQUARE,
                z_min_pm=0,
                z_max_pm=1,
                material="si",
                sidewall_angle_deg=angle,
            )


def test_segments_and_windows_are_ordered_and_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="z_min_pm < z_max_pm"):
        StackSegment(z_min_pm=5, z_max_pm=5, material="si", index=2.0)
    with pytest.raises(ValidationError, match="window"):
        EffectiveIndexReduction(**{**reduction().model_dump(), "window_z_min_pm": 100})
    with pytest.raises(ValidationError):
        Film(level="box", z_min_pm=0, z_max_pm=1, material="si", colour="red")  # type: ignore[call-arg]


def test_metres_converts_exactly() -> None:
    assert problem(extrusion()).metres(2) == 2e-9
