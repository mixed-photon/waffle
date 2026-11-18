"""Layout-to-physics conversion, one section per MOD-006 acceptance criterion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Rectangle
from waffle.contracts.layout.layout import Cell, Instance, InstanceArray, LayerSpec, Layout, Port
from waffle.contracts.physics.approximations import (
    EffectiveIndexReduction,
    Polarization,
    SidewallAngleIgnored,
)
from waffle.contracts.physics.problem import (
    BoundaryCondition,
    BoundaryKind,
    Extrusion3D,
    Face,
    PhysicalProblem,
    Reduced2D,
)
from waffle.contracts.technology.materials import ConstantIndex, Material
from waffle.contracts.technology.revision import (
    LayoutLayer,
    Patterned,
    TechnologyRevision,
)
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common import examples
from waffle.simulation.common.examples import request
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.common.request import (
    EffectiveIndexSpec,
    Margins,
    PhysicalRequest,
    PhysicalWindow,
)
from waffle.simulation.photonics.device_analysis.slab_reference import symmetric_transcendental

TECH = synthetic_soi()


def codes(diagnostics: tuple[Diagnostic, ...], severity: Severity = Severity.ERROR) -> list[str]:
    return [d.code for d in diagnostics if d.severity is severity]


def prepare(
    layout: Layout, req: PhysicalRequest, tech: TechnologyRevision = TECH
) -> PhysicalProblem:
    return prepare_physical_problem(layout, tech, req).unwrap()


def fails(
    layout: Layout, req: PhysicalRequest, tech: TechnologyRevision = TECH
) -> tuple[Diagnostic, ...]:
    result = prepare_physical_problem(layout, tech, req)
    assert result.value is None
    return result.diagnostics


def replace_level(tech: TechnologyRevision, name: str, **update: object) -> TechnologyRevision:
    levels = tuple(
        lv.model_copy(update=update) if lv.name == name else lv for lv in tech.stack.levels
    )
    return tech.model_copy(update={"stack": tech.stack.model_copy(update={"levels": levels})})


def extrusion(problem: PhysicalProblem) -> Extrusion3D:
    assert isinstance(problem.description, Extrusion3D)
    return problem.description


def reduced(problem: PhysicalProblem) -> Reduced2D:
    assert isinstance(problem.description, Reduced2D)
    return problem.description


# ---- Criterion 1: extrusion dimensions, material assignments, port placement ------------


def test_straight_waveguide_3d_extrusion_matches_the_hand_computed_stack() -> None:
    problem = prepare(examples.straight_waveguide(), request())
    d = extrusion(problem)
    # Strip (0, -250)..(10000, 250) nm plus 2 um lateral; 0..220 nm of patterned levels
    # plus 1 um vertical (pm).
    assert (d.domain.lo, d.domain.hi) == ((-2000, -2250), (12000, 2250))
    assert (d.domain.z_min_pm, d.domain.z_max_pm) == (-1_000_000, 1_220_000)
    assert [(f.level, f.z_min_pm, f.z_max_pm, f.material) for f in d.films] == [
        ("box", -1_000_000, 0, "sio2"),  # cut at the window; the substrate is outside it
        ("slab", 0, 90_000, "sio2"),  # patterned level: its fill covers the plane
        ("core", 90_000, 220_000, "sio2"),
        ("cladding", 220_000, 1_220_000, "sio2"),
    ]
    assert [(p.level, p.layer, p.z_min_pm, p.z_max_pm, p.material) for p in d.prisms] == [
        ("slab", "WG", 0, 90_000, "si"),
        ("core", "WG", 90_000, 220_000, "si"),
    ]
    assert d.prisms[0].polygon.points == ((0, -250), (10000, -250), (10000, 250), (0, 250))
    assert [m.name for m in d.materials] == ["si", "sio2"] and d.background == "sio2"
    assert problem.approximations == () and problem.dbu_pm == 1000
    assert all(b.kind is BoundaryKind.ABSORBING for _, b in d.domain.boundaries)


def test_rib_waveguide_stacks_the_partially_etched_levels() -> None:
    d = extrusion(prepare(examples.rib_waveguide(), request()))
    assert [(p.level, p.layer, p.z_min_pm, p.z_max_pm) for p in d.prisms] == [
        ("slab", "SLAB", 0, 90_000),
        ("slab", "WG", 0, 90_000),
        ("core", "WG", 90_000, 220_000),
    ]
    assert (d.domain.lo, d.domain.hi) == ((-2000, -3500), (12000, 3500))


def test_port_planes_carry_layout_identity_and_the_waveguide_z_span() -> None:
    d = extrusion(prepare(examples.straight_waveguide(), request()))
    assert [
        (p.path, p.layer, p.center, p.width, p.orientation, p.z_min_pm, p.z_max_pm, p.domain)
        for p in d.ports
    ] == [
        ("o1", "WG", (0, 0), 500, 180, 0, 220_000, Domain.OPTICAL),
        ("o2", "WG", (10000, 0), 500, 0, 0, 220_000, Domain.OPTICAL),
    ]


def test_hierarchy_is_flattened_with_transforms_and_port_paths() -> None:
    layout = examples.hierarchical_bend()
    d = extrusion(prepare(layout, request()))
    assert [p.path for p in d.ports] == ["in", "out"]  # the cell's own ports by default
    assert [p.polygon.points for p in d.prisms if p.level == "core"] == [
        ((0, -250), (5000, -250), (5000, 250), (0, 250)),
        ((4750, 0), (5250, 0), (5250, 5000), (4750, 5000)),  # b: rotated 90 deg, at (5000, 0)
    ]
    assert (d.domain.lo, d.domain.hi) == ((-2000, -2250), (7250, 7000))
    nested = extrusion(prepare(layout, request(ports=("b/o2", "a/o1"))))
    assert [(p.path, p.center, p.orientation) for p in nested.ports] == [
        ("a/o1", (0, 0), 180),
        ("b/o2", (5000, 5000), 90),
    ]


def test_sidewall_angle_is_kept_in_3d() -> None:
    tech = replace_level(TECH, "core", sidewall_angle=Quantity(value=80, unit="deg"))
    problem = prepare(examples.straight_waveguide(), request(), tech)
    angles = {p.level: p.sidewall_angle_deg for p in extrusion(problem).prisms}
    assert angles == {"slab": None, "core": 80.0}
    assert problem.approximations == ()


def test_zero_thickness_level_is_an_extrusion_error() -> None:
    tech = replace_level(TECH, "core", thickness=Quantity(value=0, unit="nm"))
    found = fails(examples.straight_waveguide(), request(), tech)
    assert "physics.extrusion.thickness" in codes(found)
    assert next(d for d in found if d.code == "physics.extrusion.thickness").location == (
        "stack",
        "levels",
        3,
        "thickness",
    )


def test_level_material_must_be_defined() -> None:
    tech = replace_level(TECH, "core", material="germanium")
    found = fails(examples.straight_waveguide(), request(), tech)
    assert "physics.extrusion.material_unknown" in codes(found)


def test_levels_must_have_whole_picometre_bounds() -> None:
    tech = replace_level(TECH, "core", thickness=Quantity(value=0.0001, unit="nm"))
    assert "physics.length.sub_picometre" in codes(
        fails(examples.straight_waveguide(), request(), tech)
    )


def with_ports(layout: Layout, *ports: Port) -> Layout:
    cell = layout.cells[0].model_copy(update={"ports": ports})
    return layout.model_copy(update={"cells": (cell,)})


def port(name: str, x: int, y: int, orientation: int, layer: str = "WG", width: int = 500) -> Port:
    return Port(
        name=name,
        center=(x, y),
        width=width,
        orientation=orientation,  # type: ignore[arg-type]
        layer=layer,
        domain=Domain.OPTICAL,
    )


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (port("p", 5000, 0, 0), "physics.port.not_on_geometry"),
        (port("p", 0, 0, 0), "physics.port.orientation"),
        (port("p", 0, 0, 180, width=400), "physics.port.width_mismatch"),
        (port("p", 0, 0, 180, layer="PORT"), "physics.port.layer_unpatterned"),
        (port("p", 90000, 0, 0), "physics.port.outside_domain"),
    ],
)
def test_port_placement_errors(bad: Port, code: str) -> None:
    found = fails(with_ports(examples.straight_waveguide(), bad), request())
    assert code in codes(found)
    assert next(d for d in found if d.code == code).location[:2] == ("ports", "p") or (
        code == "physics.port.layer_unpatterned"
    )


def test_port_inside_a_declared_absorber_is_an_error() -> None:
    thick = BoundaryCondition(thickness_pm=3_000_000)  # 3 um absorber, 2 um margin
    found = fails(examples.straight_waveguide(), request(boundaries={Face.X_MAX: thick}))
    assert codes(found) == ["physics.port.in_absorber"]
    assert found[0].location == ("ports", "o2")
    thin = BoundaryCondition(thickness_pm=1_000_000)
    assert prepare(examples.straight_waveguide(), request(boundaries={Face.X_MAX: thin}))


def test_unknown_selected_port_is_reported() -> None:
    found = fails(examples.straight_waveguide(), request(ports=("o1", "nope")))
    assert codes(found) == ["physics.port.unknown"]


def test_no_geometry_on_patterning_layers_is_an_empty_domain() -> None:
    layout = examples.straight_waveguide()
    cell = Cell(name="straight", shapes={"PORT": (Rectangle(lo=(0, 0), hi=(10, 10)),)})
    found = fails(layout.model_copy(update={"cells": (cell,)}), request())
    assert codes(found) == ["physics.domain.empty"]


def test_request_quantities_are_checked() -> None:
    layout = examples.straight_waveguide()

    def with_margins(lateral: float, vertical: float, unit: str = "um") -> PhysicalRequest:
        return request(
            margins=Margins(
                lateral=Quantity(value=lateral, unit=unit),
                vertical=Quantity(value=vertical, unit=unit),
            )
        )

    assert codes(fails(layout, with_margins(-1, 1))) == ["physics.margin.negative"]
    assert codes(fails(layout, with_margins(0.0005, 1))) == ["physics.margin.off_grid"]
    assert codes(fails(layout, with_margins(1, 0))) == ["physics.margin.nonpositive"]
    assert codes(fails(layout, with_margins(1, 1, "deg"))) == ["units.dimension_mismatch"] * 2
    assert codes(fails(layout, with_margins(1, 0.0000001))) == ["physics.length.sub_picometre"]
    zero_lateral = extrusion(prepare(layout, with_margins(0, 1)))
    assert (zero_lateral.domain.lo, zero_lateral.domain.hi) == ((0, -250), (10000, 250))


def test_boundary_conditions_are_validated_and_recorded() -> None:
    layout = examples.straight_waveguide()
    mixed = request(
        boundaries={
            Face.Y_MIN: BoundaryCondition(kind=BoundaryKind.PEC),
            Face.Y_MAX: BoundaryCondition(kind=BoundaryKind.PEC),
            Face.Z_MIN: BoundaryCondition(kind=BoundaryKind.SYMMETRIC),
        }
    )
    boundaries = extrusion(prepare(layout, mixed)).domain.boundaries
    assert (boundaries.y_min.kind, boundaries.z_min.kind, boundaries.x_min.kind) == (
        BoundaryKind.PEC,
        BoundaryKind.SYMMETRIC,
        BoundaryKind.ABSORBING,
    )
    periodic = BoundaryCondition(kind=BoundaryKind.PERIODIC)
    assert codes(fails(layout, request(boundaries={Face.Y_MIN: periodic}))) == [
        "physics.boundary.periodic_unpaired"
    ]
    assert codes(fails(layout, request("2d", boundaries={Face.Z_MAX: periodic}))) == [
        "physics.boundary.face_unsupported",
        "physics.boundary.periodic_unpaired",
    ]
    with pytest.raises(ValidationError, match="thickness_pm applies to absorbing"):
        BoundaryCondition(kind=BoundaryKind.PEC, thickness_pm=1)


# ---- Criterion 2: references to the source layout and technology ------------------------


def test_problem_pins_the_exact_source_revisions_and_survives_serialization() -> None:
    layout = examples.straight_waveguide()
    pinned = RevisionRef.of(EntityId("design", "wg_100um"), {"resolved": "values"})
    problem = prepare(layout, request(design=pinned, metadata={"purpose": "unit test"}))
    assert problem.layout == revision_of(layout) and problem.technology == revision_of(TECH)
    assert problem.design == pinned and problem.top_cell == "straight"
    assert problem.id == EntityId("physical_problem", "problem")
    loaded = load_record(to_canonical_json(problem), PhysicalProblem).unwrap()
    assert loaded == problem
    assert revision_of(loaded) == revision_of(problem)
    assert (loaded.layout, loaded.technology, loaded.design) == (
        revision_of(layout),
        revision_of(TECH),
        pinned,
    )


def test_a_different_cell_of_the_same_layout_is_recorded() -> None:
    layout = examples.hierarchical_bend()
    problem = prepare(layout, request(cell="leaf"))
    assert problem.top_cell == "leaf" and problem.layout == revision_of(layout)
    assert [p.path for p in extrusion(problem).ports] == ["o1", "o2"]


def test_unknown_cell_is_diagnosed() -> None:
    assert "layout.cell.unknown" in codes(fails(examples.straight_waveguide(), request(cell="x")))


def test_invalid_layout_and_technology_diagnostics_are_returned() -> None:
    layout = examples.straight_waveguide().model_copy(update={"top": "missing"})
    assert "layout.top.unknown" in codes(fails(layout, request()))
    tech = TECH.model_copy(update={"dbu_pm": 3000})
    assert "technology.grid_mismatch" in codes(
        fails(examples.straight_waveguide(), request(), tech)
    )


# ---- Criterion 3: missing or ambiguous layer mappings -----------------------------------


def with_layer(layout: Layout, spec: LayerSpec, layer: str) -> Layout:
    cell = layout.cells[0]
    shapes = {**cell.shapes, layer: (Rectangle(lo=(0, 0), hi=(100, 100)),)}
    return layout.model_copy(
        update={
            "layers": (*layout.layers, spec),
            "cells": (cell.model_copy(update={"shapes": shapes}),),
        }
    )


def test_layer_missing_from_the_technology_is_diagnosed_with_its_location() -> None:
    layout = with_layer(
        examples.straight_waveguide(), LayerSpec(name="M9", layer=9, datatype=0), "M9"
    )
    found = fails(layout, request())
    assert codes(found) == ["physics.layer.unknown"]
    assert found[0].location == ("layers", "M9")


def test_layer_with_a_different_gds_pair_is_diagnosed() -> None:
    layout = examples.straight_waveguide()
    layout = layout.model_copy(
        update={
            "layers": tuple(
                LayerSpec(name=s.name, layer=11, datatype=0) if s.name == "WG" else s
                for s in layout.layers
            )
        }
    )
    assert codes(fails(layout, request())) == ["physics.layer.gds_mismatch"]


def test_ambiguous_technology_layers_are_diagnosed() -> None:
    duplicate_name = TECH.model_copy(
        update={"layers": (*TECH.layers, LayoutLayer(name="WG", gds_layer=77, gds_datatype=0))}
    )
    found = codes(fails(examples.straight_waveguide(), request(), duplicate_name))
    assert "physics.layer.ambiguous" in found and "technology.duplicate_layer_name" in found
    duplicate_pair = TECH.model_copy(
        update={"layers": (*TECH.layers, LayoutLayer(name="WG2", gds_layer=1, gds_datatype=0))}
    )
    found = codes(fails(examples.straight_waveguide(), request(), duplicate_pair))
    assert "physics.layer.ambiguous" in found and "technology.duplicate_gds_pair" in found


def test_drawing_layer_that_patterns_no_level_is_an_error_only_when_drawn() -> None:
    extra = LayoutLayer(name="DEEP", gds_layer=3, gds_datatype=0)
    tech = TECH.model_copy(update={"layers": (*TECH.layers, extra)})
    unused = examples.straight_waveguide().model_copy(
        update={"layers": (*examples.LAYERS, LayerSpec(name="DEEP", layer=3, datatype=0))}
    )
    result = prepare_physical_problem(unused, tech, request())
    assert result.ok  # declared but unused: the technology's own warning is enough
    drawn = with_layer(
        examples.straight_waveguide(), LayerSpec(name="DEEP", layer=3, datatype=0), "DEEP"
    )
    found = fails(drawn, request(), tech)
    assert "physics.layer.unpatterned" in codes(found)
    assert "technology.layer_unmapped" in codes(found, Severity.WARNING)


def test_annotation_layers_are_ignored_without_a_diagnostic() -> None:
    layout = with_layer(
        examples.straight_waveguide(), LayerSpec(name="DEVREC", layer=68, datatype=0), "DEVREC"
    )
    result = prepare_physical_problem(layout, TECH, request())
    assert result.ok and result.diagnostics == ()
    assert all(p.layer != "DEVREC" for p in extrusion(result.unwrap()).prisms)


def test_off_manufacturing_grid_vertices_warn_and_incompatible_dbu_fails() -> None:
    layout = examples.straight_waveguide()
    cell = layout.cells[0].model_copy(
        update={
            "shapes": {
                "WG": (Rectangle(lo=(0, -250), hi=(10000, 250)),),
                "SLAB": (Rectangle(lo=(0, -1503), hi=(10000, 1500)),),
            }
        }
    )
    result = prepare_physical_problem(layout.model_copy(update={"cells": (cell,)}), TECH, request())
    assert result.ok and codes(result.diagnostics, Severity.WARNING) == [
        "physics.grid.off_manufacturing"
    ]
    coarse = layout.model_copy(update={"grid": Grid(dbu_pm=2000)})
    assert "physics.grid.incompatible" in codes(fails(coarse, request()))


def test_finer_layout_grid_is_checked_against_the_manufacturing_grid() -> None:
    layout = examples.straight_waveguide()
    fine = Layout(
        id=layout.id,
        grid=Grid(dbu_pm=500),
        top="straight",
        layers=layout.layers,
        cells=(
            Cell(
                name="straight",
                shapes={"WG": (Rectangle(lo=(0, -500), hi=(20000, 500)),)},
                ports=(port("o1", 0, 0, 180, width=1000),),
            ),
        ),
    )
    problem = prepare(fine, request())
    assert problem.dbu_pm == 500
    assert (extrusion(problem).domain.lo, extrusion(problem).domain.hi) == (
        (-4000, -4500),
        (24000, 4500),
    )
    assert problem.metres(1) == 5e-10


# ---- Criterion 4: 2D approximations versus 3D descriptions ------------------------------


def test_3d_and_2d_problems_are_different_variants_with_explicit_approximations() -> None:
    layout = examples.straight_waveguide()
    p3 = prepare(layout, request("3d"))
    p2 = prepare(layout, request("2d"))
    assert isinstance(p3.description, Extrusion3D) and p3.approximations == ()
    assert isinstance(p2.description, Reduced2D)
    (reduction,) = p2.approximations
    assert isinstance(reduction, EffectiveIndexReduction)
    assert (reduction.method, reduction.polarization, reduction.mode_order) == (
        "slab_transfer_matrix",
        Polarization.TE,
        0,
    )
    assert reduction.wavelength_pm == 1_550_000 and "wavelength" in reduction.validity
    assert (reduction.window_z_min_pm, reduction.window_z_max_pm) == (-1_000_000, 1_220_000)
    for text, expected in (
        (to_canonical_json(p3), Extrusion3D),
        (to_canonical_json(p2), Reduced2D),
    ):
        loaded = load_record(text, PhysicalProblem).unwrap()
        assert isinstance(loaded.description, expected)
    assert p3.model_dump()["description"]["dimensionality"] == "3d"
    assert p2.model_dump()["description"]["dimensionality"] == "2d"


def test_2d_regions_carry_the_effective_indices_of_the_reduction() -> None:
    problem = prepare(examples.straight_waveguide(), request("2d"))
    d = reduced(problem)
    # Fused silica (Malitson) evaluated here from the published coefficients at 1.55 um.
    b, c = (0.6961663, 0.4079426, 0.8974794), (0.0684043**2, 0.1162414**2, 9.896161**2)
    n_ox = (1 + sum(bi * 1.55**2 / (1.55**2 - ci) for bi, ci in zip(b, c, strict=True))) ** 0.5
    te0 = symmetric_transcendental(3.476, n_ox, 220e-9, 1.55e-6, Polarization.TE)
    assert d.background_index == pytest.approx(n_ox, abs=1e-11)
    assert [(r.layer, r.index) for r in d.regions] == [("WG", pytest.approx(te0, abs=1e-9))]
    assert 2.8 <= te0 <= 2.85
    assert (d.domain.lo, d.domain.hi) == ((-2000, -2250), (12000, 2250))
    assert [(p.path, p.center, p.orientation) for p in d.ports] == [
        ("o1", (0, 0), 180),
        ("o2", (10000, 0), 0),
    ]
    reduction = problem.approximations[0]
    assert isinstance(reduction, EffectiveIndexReduction)
    column = reduction.column("WG")
    assert (
        column is not None and column.rule == "guided_mode" and column.n_eff == d.regions[0].index
    )
    background = reduction.column(None)
    assert background is not None and background.rule == "cladding_cutoff"


def test_2d_rib_paints_the_slab_region_below_the_core_region() -> None:
    d = reduced(prepare(examples.rib_waveguide(), request("2d")))
    assert [r.layer for r in d.regions] == ["SLAB", "WG"]
    assert d.background_index < d.regions[0].index < d.regions[1].index
    tm = reduced(
        prepare(
            examples.rib_waveguide(),
            request(
                "2d",
                reduction=EffectiveIndexSpec(
                    wavelength=Quantity(value=1.55, unit="um"), polarization=Polarization.TM
                ),
            ),
        )
    )
    assert tm.regions[1].index < d.regions[1].index  # n_TE > n_TM


def test_2d_without_a_reduction_and_3d_with_one_are_errors_not_defaults() -> None:
    layout = examples.straight_waveguide()
    no_reduction = request("3d").model_copy(update={"dimensionality": "2d"})
    assert codes(fails(layout, no_reduction)) == ["physics.reduction.required"]
    spec = EffectiveIndexSpec(
        wavelength=Quantity(value=1.55, unit="um"), polarization=Polarization.TE
    )
    assert codes(fails(layout, request("3d", reduction=spec))) == ["physics.reduction.unexpected"]


def test_records_cannot_blur_the_dimensionality() -> None:
    p2 = prepare(examples.straight_waveguide(), request("2d"))
    p3 = prepare(examples.straight_waveguide(), request("3d"))
    with pytest.raises(ValidationError, match="exactly one effective-index reduction"):
        PhysicalProblem.model_validate({**p2.model_dump(), "approximations": ()})
    with pytest.raises(ValidationError, match="3D description cannot carry"):
        PhysicalProblem.model_validate(
            {**p3.model_dump(), "approximations": p2.model_dump()["approximations"]}
        )
    with pytest.raises(ValidationError, match="equal the reduction's"):
        data = p2.model_dump()
        data["description"]["background_index"] = 2.0
        PhysicalProblem.model_validate(data)


def test_2d_records_ignored_sidewalls_and_absorbing_materials_are_unsupported() -> None:
    tech = replace_level(TECH, "core", sidewall_angle=Quantity(value=80, unit="deg"))
    problem = prepare(examples.straight_waveguide(), request("2d"), tech)
    assert problem.approximations[1] == SidewallAngleIgnored(levels=("core",))
    lossy = tuple(
        Material(
            name=m.name,
            optical=ConstantIndex(n=3.476, k=0.01),
            validity=m.validity,
            source=m.source,
        )
        if m.name == "si"
        else m
        for m in TECH.materials
    )
    found = fails(
        examples.straight_waveguide(), request("2d"), TECH.model_copy(update={"materials": lossy})
    )
    assert codes(found) == ["physics.reduction.absorbing_material"]


def test_2d_layers_with_non_nested_levels_cannot_be_painted() -> None:
    split = replace_level(
        replace_level(TECH, "slab", pattern=Patterned(layers=("SLAB",), fill="sio2")),
        "core",
        pattern=Patterned(layers=("WG",), fill="sio2"),
    )
    assert prepare_physical_problem(examples.rib_waveguide(), split, request("3d")).ok
    assert "physics.reduction.layers_not_nested" in codes(
        fails(examples.rib_waveguide(), request("2d"), split)
    )


def test_2d_reduction_diagnostics_are_forwarded_with_the_problem() -> None:
    deep = request(
        "2d",
        margins=Margins(
            lateral=Quantity(value=2, unit="um"), vertical=Quantity(value=3, unit="um")
        ),
    )
    result = prepare_physical_problem(examples.straight_waveguide(), TECH, deep)
    # A window reaching the silicon substrate leaves no guided mode: reported, and recorded.
    assert codes(result.diagnostics, Severity.WARNING) == ["physics.reduction.no_guided_mode"]
    core = reduced(result.unwrap()).regions[0]
    assert core.index == 3.476


def test_2d_wavelength_outside_material_validity_is_an_error() -> None:
    spec = EffectiveIndexSpec(
        wavelength=Quantity(value=2.0, unit="um"), polarization=Polarization.TE
    )
    found = fails(examples.straight_waveguide(), request("2d", reduction=spec))
    assert "technology.material_out_of_range" in codes(found)


def test_window_is_a_strict_3d_domain_choice() -> None:
    window = PhysicalWindow(lo=(0, -250), hi=(1000, 250), z_min_pm=0, z_max_pm=220_000)
    direct = PhysicalRequest(
        id=EntityId("physical_problem", "local"), dimensionality="3d", window=window
    )
    assert direct.margins is None and "margins" not in direct.model_dump()
    assert PhysicalRequest.model_validate_json(direct.model_dump_json()) == direct
    assert "window" not in request().model_dump()
    layout = examples.straight_waveguide()
    assert codes(fails(layout, request(margins=None))) == ["physics.domain.required"]
    assert codes(fails(layout, request(window=window))) == ["physics.domain.conflicting"]
    assert codes(fails(layout, request("2d", margins=None, window=window))) == [
        "physics.window.unsupported"
    ]
    for update in (
        {"lo": (1000, -250)},
        {"hi": (1000, -251)},
        {"hi": (1000.0, 250)},
        {"z_min_pm": 220_000},
        {"z_max_pm": True},
    ):
        with pytest.raises(ValidationError):
            PhysicalWindow.model_validate({**window.model_dump(), **update})


def test_missing_domain_is_reported_before_expanding_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_flatten(*args: object, **kwargs: object) -> None:
        pytest.fail("domain choice must be known before flattening")

    monkeypatch.setattr("waffle.simulation.common.prepare.flatten", unexpected_flatten)
    layout = examples.hierarchical_bend()
    assert codes(fails(layout, request(margins=None))) == ["physics.domain.required"]


def test_window_cuts_films_and_preserves_sloped_prism_origins() -> None:
    layout = examples.straight_waveguide()
    tech = replace_level(TECH, "core", sidewall_angle=Quantity(value=80, unit="deg"))
    window = PhysicalWindow(lo=(100, 200), hi=(200, 260), z_min_pm=125_000, z_max_pm=150_000)
    problem = prepare(layout, request(margins=None, window=window), tech)
    d = extrusion(problem)
    assert d.domain.model_dump(exclude={"boundaries"}) == window.model_dump()
    assert [(f.level, f.z_min_pm, f.z_max_pm) for f in d.films] == [("core", 125_000, 150_000)]
    (core,) = d.prisms
    assert core.level == "core" and core.z_min_pm == 90_000 and core.z_max_pm == 220_000
    assert core == extrusion(prepare(layout, request(), tech)).prisms[-1]
    assert problem.layout == revision_of(layout) and problem.technology == revision_of(tech)
    assert load_record(to_canonical_json(problem), PhysicalProblem).unwrap() == problem


def test_window_with_no_drawn_geometry_can_hold_blanket_material_or_only_background() -> None:
    layout = examples.straight_waveguide()
    window = PhysicalWindow(lo=(100, 1000), hi=(200, 1200), z_min_pm=-200_000, z_max_pm=-100_000)
    d = extrusion(prepare(layout, request(margins=None, window=window)))
    assert [(f.level, f.material, f.z_min_pm, f.z_max_pm) for f in d.films] == [
        ("box", "sio2", -200_000, -100_000)
    ]
    assert not d.prisms and not d.ports
    above = window.model_copy(update={"z_min_pm": 10_000_000, "z_max_pm": 11_000_000})
    d = extrusion(prepare(layout, request(margins=None, window=above)))
    assert not d.films and not d.prisms and [m.name for m in d.materials] == [d.background]


def test_window_default_ports_belong_to_the_selected_cell() -> None:
    layout = examples.hierarchical_bend()
    window = PhysicalWindow(lo=(0, -250), hi=(1000, 250), z_min_pm=0, z_max_pm=220_000)
    default = request(margins=None, window=window)
    assert [p.path for p in extrusion(prepare(layout, default)).ports] == ["in"]
    leaf = default.model_copy(update={"cell": "leaf"})
    assert [p.path for p in extrusion(prepare(layout, leaf)).ports] == ["o1"]


@pytest.mark.parametrize("clip", [{"lo": (0, -249)}, {"z_min_pm": 1}, {"z_max_pm": 219_999}])
def test_window_handles_ports_that_straddle_a_face(clip: dict[str, object]) -> None:
    layout = examples.straight_waveguide()
    window = PhysicalWindow(lo=(0, -250), hi=(1000, 250), z_min_pm=0, z_max_pm=220_000)
    clipped = window.model_copy(update=clip)
    default = request(margins=None, window=clipped)
    assert extrusion(prepare(layout, default)).ports == ()
    explicit = default.model_copy(update={"ports": ("o1",)})
    (diagnostic,) = fails(layout, explicit)
    assert diagnostic.code == "physics.port.outside_domain"
    assert diagnostic.location == ("ports", "o1")


def test_window_resolves_explicit_array_port_paths_without_expanding_the_array() -> None:
    layout = examples.straight_waveguide()
    array = InstanceArray(
        columns=1_000_000, rows=1_000_000, column_step=(20_000, 0), row_step=(0, 1000)
    )
    top = Cell(name="array", instances=(Instance(name="copy", cell=layout.top, array=array),))
    layout = layout.model_copy(update={"top": top.name, "cells": (top, *layout.cells)})
    x, y = 999_999 * 20_000, 500_000 * 1000
    window = PhysicalWindow(lo=(x, y - 250), hi=(x + 1000, y + 250), z_min_pm=0, z_max_pm=220_000)
    default = request(margins=None, window=window)
    assert extrusion(prepare(layout, default)).ports == ()
    inside, outside, missing = "copy/500000/999999/o1", "copy/0/0/o1", "copy/0/1000000/o1"
    (port,) = extrusion(prepare(layout, default.model_copy(update={"ports": (inside,)}))).ports
    assert port.path == inside and port.center == (x, y)
    diagnostics = fails(layout, default.model_copy(update={"ports": (outside, missing)}))
    assert [(d.code, d.location) for d in diagnostics] == [
        ("physics.port.unknown", ("ports", missing)),
        ("physics.port.outside_domain", ("ports", outside)),
    ]
