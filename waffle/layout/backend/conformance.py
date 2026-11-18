"""Backend conformance cases, shared by every layout backend.

Each `Case` takes a backend and a technology and raises `AssertionError` on a violation;
`applicable_cases(backend)` keeps the cases whose capabilities the backend declares. A
backend package runs them with a couple of lines:

    @pytest.mark.parametrize("case", applicable_cases(MyBackend()), ids=lambda c: c.name)
    def test_conformance(case: Case) -> None:
        case.run(MyBackend(), synthetic_soi())

The fake backend runs them in the fast suite; the gdsfactory adapter runs them against the
real library; a native backend (NAT-001) runs the same ones. The technology must have the
layers the backend's generators need (the synthetic fixture does).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from waffle.contracts.component.definition import Domain, GeneratorSource
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Polygon, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout, Port
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.backend.interface import (
    BackendCode,
    BackendInfo,
    BooleanOp,
    BooleanOperation,
    Capability,
    GenerationRequest,
    ImportRequest,
    LayoutBackend,
    exporter_of,
    generator_of,
    geometry_of,
    importer_of,
    layer_table,
)
from waffle.layout.backend.packaging import export_package
from waffle.layout.formats.sidecar import verify_sidecar
from waffle.layout.services.compare import CompareMode, compare_layouts
from waffle.layout.services.validate import validate_layout


@dataclass(frozen=True)
class Case:
    name: str
    requires: frozenset[Capability]
    run: Callable[[LayoutBackend, TechnologyRevision], None]


def _need[T](result: Result[T]) -> T:
    assert result.value is not None, "; ".join(str(d) for d in result.diagnostics)
    return result.value


def _codes(diagnostics: Iterable[Diagnostic]) -> list[str]:
    return [d.code for d in diagnostics]


def _request(
    info: BackendInfo, technology: TechnologyRevision, index: int = 0
) -> GenerationRequest:
    generator = info.generators[index]
    return GenerationRequest(
        generator=GeneratorSource(name=generator.name, version=generator.version),
        values=generator.example,
        technology=technology,
    )


def _generated(backend: LayoutBackend, technology: TechnologyRevision) -> list[Layout]:
    """One layout per generator from its example values; none if the backend cannot generate."""
    if Capability.GENERATION not in backend.info().capabilities:
        return []
    generator = _need(generator_of(backend))
    return [
        _need(generator.generate(_request(backend.info(), technology, i)))
        for i in range(len(backend.info().generators))
    ]


def _replace_cell(layout: Layout, name: str, **update: object) -> Layout:
    cells = tuple(c.model_copy(update=update) if c.name == name else c for c in layout.cells)
    return layout.model_copy(update={"cells": cells})


def _strip_ports(layout: Layout) -> Layout:
    return layout.model_copy(
        update={"cells": tuple(c.model_copy(update={"ports": ()}) for c in layout.cells)}
    )


def _twice_area(cell: Cell, layer: str) -> int:
    total = 0
    for shape in cell.shapes.get(layer, ()):
        ring = to_polygon(shape).unwrap().points
        total += sum(
            ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring))
        )
    return total


def ops_layout() -> Layout:
    """Overlapping rectangles on layers A and B, with a child cell, for geometry operations.

    Top: A = (0,0)-(10,10) and (5,0)-(15,10), B = (8,0)-(20,10), so A merges to (0,0)-(15,10)
    and A and B overlap in (8,0)-(15,10). B spans the same y range as A, so no Boolean result
    has a hole."""

    def rect(x0: int, y0: int, x1: int, y1: int) -> Rectangle:
        return Rectangle(lo=(x0, y0), hi=(x1, y1))

    child = Cell(
        name="child",
        shapes={"A": (rect(0, 0, 4, 2), rect(2, 0, 8, 2))},
        ports=(
            Port(
                name="p1", center=(0, 1), width=2, orientation=180, layer="A", domain=Domain.OPTICAL
            ),
        ),
    )
    top = Cell(
        name="top",
        shapes={"A": (rect(0, 0, 10, 10), rect(5, 0, 15, 10)), "B": (rect(8, 0, 20, 10),)},
        instances=(
            Instance(
                name="c1", cell="child", transform=Transform(rotation=90, translation=(30, 0))
            ),
        ),
    )
    return Layout(
        id=EntityId("layout", "ops"),
        grid=Grid(dbu_pm=1000),
        top="top",
        layers=tuple(LayerSpec(name=n, layer=i, datatype=0) for i, n in enumerate("ABC", 1)),
        cells=(top, child),
    )


# Cases


def _capabilities_are_consistent(backend: LayoutBackend, technology: TechnologyRevision) -> None:
    info = backend.info()
    assert info.capabilities, "a backend must declare at least one capability"
    assert info.producer.name and info.producer.version
    names = [(g.name, g.version) for g in info.generators]
    assert len(names) == len(set(names)), "generator names and versions must be unique"
    for capability, lookup in (
        (Capability.GENERATION, generator_of),
        (Capability.IMPORT, importer_of),
        (Capability.EXPORT, exporter_of),
        (Capability.GEOMETRY, geometry_of),
    ):
        result = lookup(backend)
        if capability in info.capabilities:
            assert result.value is backend, f"declared {capability} is not implemented"
        else:
            assert result.value is None
            assert _codes(result.diagnostics) == [BackendCode.CAPABILITY_UNSUPPORTED]


def _generators_produce_valid_layouts(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    generator = _need(generator_of(backend))
    table = {(s.name, s.layer, s.datatype) for s in layer_table(technology)}
    for index, info in enumerate(backend.info().generators):
        request = _request(backend.info(), technology, index)
        layout = _need(generator.generate(request))
        where = f"{info.name} {info.version}"
        assert validate_layout(layout) == [], where
        assert layout.grid.dbu_pm == technology.dbu_pm, where
        assert {(s.name, s.layer, s.datatype) for s in layout.layers} == table, where
        top = next(c for c in layout.cells if c.name == layout.top)
        assert top.ports, f"{where}: the top cell has no ports"
        assert layout.metadata.get("generator") == info.name, where
        assert layout.metadata.get("generator_version") == info.version, where
        again = _need(generator.generate(request))
        assert revision_of(again) == revision_of(layout), (
            f"{where}: generation is not deterministic"
        )


def _generation_rejects_invalid_requests(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    generator = _need(generator_of(backend))
    info = backend.info()
    base = _request(info, technology)
    name, version = base.generator.name, base.generator.version
    for bad in (
        GeneratorSource(name=name + "_x", version=version),
        GeneratorSource(name=name, version=version + ".9"),
    ):
        result = generator.generate(base.model_copy(update={"generator": bad}))
        assert result.value is None and _codes(result.diagnostics) == [
            BackendCode.GENERATOR_UNKNOWN
        ]
    values = dict(base.values.values)
    extra = base.values.model_copy(
        update={"values": {**values, "bogus": Quantity(value=1, unit="um")}}
    )
    result = generator.generate(base.model_copy(update={"values": extra}))
    assert result.value is None and "params.unknown" in _codes(result.diagnostics)
    first = info.generators[0].parameters.parameters[0].name
    wrong = base.values.model_copy(
        update={"values": {**values, first: Quantity(value=1, unit="s")}}
    )
    result = generator.generate(base.model_copy(update={"values": wrong}))
    assert result.value is None and "units.dimension_mismatch" in _codes(result.diagnostics)
    for spec in info.generators[0].parameters.parameters:
        if spec.default is None:
            missing = base.values.model_copy(
                update={"values": {k: v for k, v in values.items() if k != spec.name}}
            )
            result = generator.generate(base.model_copy(update={"values": missing}))
            assert result.value is None and "params.missing" in _codes(result.diagnostics)


def _gds_round_trip_preserves_layout(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    exporter, importer = _need(exporter_of(backend)), _need(importer_of(backend))
    for layout in [nested_layout(), *_generated(backend, technology)]:
        package = _need(export_package(exporter, layout))
        assert verify_sidecar(package.gds, package.sidecar) == []
        assert package.sidecar.layout == revision_of(layout)
        imported = _need(importer.import_gds(package.gds, ImportRequest(sidecar=package.sidecar)))
        for mode in CompareMode:
            assert compare_layouts(layout, imported, mode) == [], f"{layout.id} ({mode})"
        assert imported.id == layout.id


def _import_without_sidecar_has_no_ports(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    exporter, importer = _need(exporter_of(backend)), _need(importer_of(backend))
    layout = nested_layout()
    package = _need(export_package(exporter, layout))
    request = ImportRequest(layers=layout.layers, layout_id=EntityId("layout", "bare"))
    result = importer.import_gds(package.gds, request)
    bare = _need(result)
    assert any(
        d.code == BackendCode.NO_PORTS and d.severity is Severity.INFO for d in result.diagnostics
    )
    assert all(not cell.ports for cell in bare.cells)
    assert compare_layouts(_strip_ports(layout), bare, CompareMode.FLAT) == []


def _import_rejects_modified_bytes(backend: LayoutBackend, technology: TechnologyRevision) -> None:
    exporter, importer = _need(exporter_of(backend)), _need(importer_of(backend))
    package = _need(export_package(exporter, nested_layout()))
    flipped = bytearray(package.gds)
    flipped[-10] ^= 0xFF
    result = importer.import_gds(bytes(flipped), ImportRequest(sidecar=package.sidecar))
    assert result.value is None and "gds.sidecar.hash_mismatch" in _codes(result.diagnostics)


def _merge_unions_overlapping_shapes(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    operations = _need(geometry_of(backend))
    layout = ops_layout()
    merged = _need(operations.merge(layout))
    expected = _replace_cell(
        _replace_cell(layout, "child", shapes={"A": (Rectangle(lo=(0, 0), hi=(8, 2)),)}),
        "top",
        shapes={
            "A": (Rectangle(lo=(0, 0), hi=(15, 10)),),
            "B": (Rectangle(lo=(8, 0), hi=(20, 10)),),
        },
    )
    assert compare_layouts(expected, merged) == []
    assert _need(operations.merge(merged)) == merged, "merge is not idempotent"


def _boolean_operations(backend: LayoutBackend, technology: TechnologyRevision) -> None:
    operations = _need(geometry_of(backend))
    layout = ops_layout()
    # Twice the area, in dbu squared, of the result on C in (top, child).
    expected = {
        BooleanOp.AND: (2 * 70, 0),
        BooleanOp.OR: (2 * 200, 2 * 16),
        BooleanOp.NOT: (2 * 80, 2 * 16),
        BooleanOp.XOR: (2 * 130, 2 * 16),
    }
    for op, (top_area, child_area) in expected.items():
        result = _need(operations.boolean(layout, BooleanOperation(op=op, a="A", b="B", out="C")))
        cells = {c.name: c for c in result.cells}
        assert (_twice_area(cells["top"], "C"), _twice_area(cells["child"], "C")) == (
            top_area,
            child_area,
        ), op
        assert validate_layout(result) == []
        assert compare_layouts(_strip_shapes(layout, "C"), _strip_shapes(result, "C")) == []
    both = _need(
        operations.boolean(layout, BooleanOperation(op=BooleanOp.AND, a="A", b="B", out="C"))
    )
    top = next(c for c in both.cells if c.name == "top")
    assert [to_polygon(s).unwrap().points for s in top.shapes["C"]] == [
        Polygon(points=((8, 0), (15, 0), (15, 10), (8, 10))).points
    ]


def _strip_shapes(layout: Layout, layer: str) -> Layout:
    return layout.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"shapes": {k: v for k, v in c.shapes.items() if k != layer}})
                for c in layout.cells
            )
        }
    )


def _boolean_reports_what_the_ir_cannot_hold(
    backend: LayoutBackend, technology: TechnologyRevision
) -> None:
    operations = _need(geometry_of(backend))
    layout = ops_layout()
    ring = _replace_cell(
        layout,
        "top",
        shapes={
            "A": (Rectangle(lo=(0, 0), hi=(10, 10)),),
            "B": (Rectangle(lo=(3, 3), hi=(6, 6)),),
        },
    )
    result = operations.boolean(ring, BooleanOperation(op=BooleanOp.NOT, a="A", b="B", out="C"))
    assert result.value is None
    assert "geometry.unsupported.polygon_holes" in _codes(result.diagnostics)
    unknown = operations.boolean(layout, BooleanOperation(op=BooleanOp.AND, a="A", b="B", out="Z"))
    assert unknown.value is None and _codes(unknown.diagnostics) == [BackendCode.LAYER_MISSING]


def _case(
    name: str, *requires: Capability, run: Callable[[LayoutBackend, TechnologyRevision], None]
) -> Case:
    return Case(name, frozenset(requires), run)


CASES: tuple[Case, ...] = (
    _case("capabilities_are_consistent", run=_capabilities_are_consistent),
    _case(
        "generators_produce_valid_layouts",
        Capability.GENERATION,
        run=_generators_produce_valid_layouts,
    ),
    _case(
        "generation_rejects_invalid_requests",
        Capability.GENERATION,
        run=_generation_rejects_invalid_requests,
    ),
    _case(
        "gds_round_trip_preserves_layout",
        Capability.EXPORT,
        Capability.IMPORT,
        run=_gds_round_trip_preserves_layout,
    ),
    _case(
        "import_without_sidecar_has_no_ports",
        Capability.EXPORT,
        Capability.IMPORT,
        run=_import_without_sidecar_has_no_ports,
    ),
    _case(
        "import_rejects_modified_bytes",
        Capability.EXPORT,
        Capability.IMPORT,
        run=_import_rejects_modified_bytes,
    ),
    _case(
        "merge_unions_overlapping_shapes", Capability.GEOMETRY, run=_merge_unions_overlapping_shapes
    ),
    _case("boolean_operations", Capability.GEOMETRY, run=_boolean_operations),
    _case(
        "boolean_reports_what_the_ir_cannot_hold",
        Capability.GEOMETRY,
        run=_boolean_reports_what_the_ir_cannot_hold,
    ),
)


def applicable_cases(backend: LayoutBackend) -> tuple[Case, ...]:
    """The cases whose required capabilities the backend declares."""
    declared = backend.info().capabilities
    return tuple(case for case in CASES if case.requires <= declared)
