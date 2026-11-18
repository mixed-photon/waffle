from __future__ import annotations

from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import Cell, LayerSpec, Layout, Port
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    ModalTransform,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.physics.problem import (
    Boundaries3D,
    BoundaryCondition,
    Domain3D,
    Extrusion3D,
    PhysicalProblem,
    Port3D,
)
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    WavelengthRange,
)
from waffle.design.mappings.validate import (
    check_connectivity,
    validate_mapping,
    validate_response_coverage,
)


def fixture() -> tuple[InterfaceDefinition, Layout, PhysicalProblem, ViewMapping]:
    interface = InterfaceDefinition(
        id=EntityId("interface", "wg"),
        terminals=(
            Terminal(name="a", domain=Domain.OPTICAL),
            Terminal(name="b", domain=Domain.OPTICAL),
        ),
    )
    ports = (
        Port(
            name="p0",
            center=(0, 0),
            width=2,
            orientation=180,
            layer="WG",
            domain=Domain.OPTICAL,
        ),
        Port(
            name="p1",
            center=(10, 0),
            width=2,
            orientation=0,
            layer="WG",
            domain=Domain.OPTICAL,
        ),
    )
    layout = Layout(
        id=EntityId("layout", "wg"),
        grid=Grid(dbu_pm=1000),
        top="top",
        layers=(LayerSpec(name="WG", layer=1, datatype=0),),
        cells=(Cell(name="top", ports=ports),),
    )
    material = Material(
        name="si",
        optical=ConstantIndex(n=3.5),
        validity=WavelengthRange(
            minimum=Quantity(value=1.2, unit="um"),
            maximum=Quantity(value=1.7, unit="um"),
        ),
        source="test",
    )
    description = Extrusion3D(
        domain=Domain3D(
            lo=(-5, -5),
            hi=(15, 5),
            z_min_pm=0,
            z_max_pm=100,
            boundaries=Boundaries3D(
                **{
                    name: BoundaryCondition()
                    for name in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
                }
            ),
        ),
        materials=(material,),
        background="si",
        films=(),
        prisms=(),
        ports=tuple(
            Port3D(
                path=p.name,
                layer=p.layer,
                domain=p.domain,
                center=p.center,
                width=p.width,
                orientation=p.orientation,
                z_min_pm=0,
                z_max_pm=100,
            )
            for p in ports
        ),
    )
    problem = PhysicalProblem(
        id=EntityId("physical_problem", "wg"),
        layout=revision_of(layout),
        top_cell="top",
        technology=RevisionRef(EntityId("technology", "t"), "sha256:" + "1" * 64),
        dbu_pm=1000,
        description=description,
    )
    bindings = tuple(
        ChannelBinding(
            terminal=t,
            layout_port=p.name,
            anchor=PortAnchor(center=p.center, orientation=p.orientation),
            simulation_port=p.name,
            model_channel=c,
        )
        for t, p, c in zip(("a", "b"), ports, ("c1", "c0"), strict=True)
    )
    responses = tuple(
        ResponseChannel(output=o, excitation=i, status=ResponseStatus.COMPUTED)
        for o in ("c0", "c1")
        for i in ("c0", "c1")
    )
    mapping = ViewMapping(
        id=EntityId("view_mapping", "wg"),
        interface=revision_of(interface),
        layout=revision_of(layout),
        problem=revision_of(problem),
        bindings=bindings,
        responses=responses,
    )
    return interface, layout, problem, mapping


def codes(diagnostics: list[Diagnostic]) -> set[str]:
    return {d.code for d in diagnostics}


def test_valid_mapping_and_explicit_channel_permutation() -> None:
    interface, layout, problem, mapping = fixture()
    assert validate_mapping(mapping, interface, layout, problem) == []
    assert tuple(b.model_channel for b in mapping.bindings) == ("c1", "c0")


def test_swapped_and_incomplete_mappings() -> None:
    interface, layout, problem, mapping = fixture()
    swapped = mapping.model_copy(
        update={
            "bindings": tuple(
                b.model_copy(
                    update={"layout_port": "p1" if b.layout_port == "p0" else "p0"}
                )
                for b in mapping.bindings
            )
        }
    )
    assert "mapping.port_swap" in codes(
        validate_mapping(swapped, interface, layout, problem)
    )
    incomplete = mapping.model_copy(update={"bindings": mapping.bindings[:1]})
    assert "mapping.missing_terminal" in codes(
        validate_mapping(incomplete, interface, layout, problem)
    )


def test_revisions_modal_transforms_and_coverage() -> None:
    interface, layout, problem, mapping = fixture()
    changed = interface.model_copy(update={"metadata": {"changed": "yes"}})
    assert "mapping.revision_mismatch" in codes(
        validate_mapping(mapping, changed, layout, problem)
    )
    binding = mapping.bindings[0].model_copy(
        update={"modal_transform": ModalTransform.MIXED_BASIS}
    )
    modal = mapping.model_copy(update={"bindings": (binding, mapping.bindings[1])})
    assert "mapping.unsupported_modal_transform" in codes(
        validate_mapping(modal, interface, layout, problem)
    )
    partial = mapping.model_copy(
        update={
            "responses": tuple(
                r for r in mapping.responses if (r.output, r.excitation) != ("c0", "c1")
            )
        }
    )
    assert validate_mapping(partial, interface, layout, problem) == []
    assert "mapping.uncomputed_response" in codes(validate_response_coverage(partial))
    assert validate_response_coverage(partial, (("c1", "c0"),)) == []


def test_rotated_and_reflected_connectivity() -> None:
    _, layout, _, mapping = fixture()

    # p1 faces east. The other p1 is placed at the same center facing west.
    def placed(transform: Transform) -> ViewMapping:
        bindings = tuple(
            b.model_copy(
                update={
                    "anchor": PortAnchor(
                        center=transform.apply(b.anchor.center),
                        orientation=transform.apply_direction(b.anchor.orientation),
                    )
                }
            )
            for b in mapping.bindings
        )
        return mapping.model_copy(update={"transform": transform, "bindings": bindings})

    other = placed(Transform(mirror=True, rotation=180, translation=(20, 0)))
    assert check_connectivity(mapping, "b", layout, other, "b", layout) == []
    rotated = placed(Transform(rotation=180, translation=(20, 0)))
    assert check_connectivity(mapping, "b", layout, rotated, "b", layout) == []
    assert "mapping.connectivity.mismatch" in codes(
        check_connectivity(mapping, "a", layout, rotated, "a", layout)
    )


def test_connectivity_rejects_stale_layout_and_different_grid() -> None:
    _, layout, _, mapping = fixture()
    changed = layout.model_copy(update={"metadata": {"revision": "new"}})
    assert "mapping.revision_mismatch" in codes(
        check_connectivity(mapping, "a", changed, mapping, "b", layout)
    )
    different_grid = layout.model_copy(update={"grid": Grid(dbu_pm=2000)})
    remapped = mapping.model_copy(update={"layout": revision_of(different_grid)})
    assert "mapping.connectivity.grid_mismatch" in codes(
        check_connectivity(mapping, "a", layout, remapped, "b", different_grid)
    )


def test_problem_top_cell_and_grid_are_checked() -> None:
    interface, layout, problem, mapping = fixture()
    wrong_grid = problem.model_copy(update={"dbu_pm": 2000})
    wrong_top = problem.model_copy(update={"top_cell": "other"})
    for changed in (wrong_grid, wrong_top):
        remapped = mapping.model_copy(update={"problem": revision_of(changed)})
        assert "mapping.source_mismatch" in codes(
            validate_mapping(remapped, interface, layout, changed)
        )


def test_partial_mapping_still_rejects_malformed_response_identities() -> None:
    interface, layout, problem, mapping = fixture()
    unknown = ResponseChannel(
        output="missing", excitation="c0", status=ResponseStatus.UNCOMPUTED
    )
    malformed = mapping.model_copy(update={"responses": (unknown, unknown)})
    findings = codes(validate_mapping(malformed, interface, layout, problem))
    assert "mapping.unknown_response_channel" in findings
    assert "mapping.duplicate_response" in findings
    assert "mapping.uncomputed_response" not in findings
