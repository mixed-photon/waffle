"""What the adapter converts, normalises, or refuses, with deliberately constructed upstream
components and foreign GDS files."""

from __future__ import annotations

import math
import struct

import klayout.db as kdb
import pytest

from third_party.gdsfactory.backend import GdsfactoryBackend
from third_party.gdsfactory.convert import clean_name, layout_from_klayout
from third_party.gdsfactory.fixtures import TABLE, convert_upstream
from third_party.gdsfactory.gds import oversized_polygons, write_bytes
from third_party.gdsfactory.generators import GENERATORS, Canvas
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import InstanceArray, Layout
from waffle.layout.backend.interface import BackendCode, ImportRequest
from waffle.layout.formats.testing import gds_bytes
from waffle.layout.services.validate import validate_layout

BACKEND = GdsfactoryBackend()
WIRE = {"length": 1000, "width": 500}


def errors(result: Result[Layout]) -> list[Diagnostic]:
    return [d for d in result.diagnostics if d.severity == "error"]


def codes(result: Result[Layout]) -> list[str]:
    return [d.code for d in errors(result)]


def straight(canvas: Canvas, name: str = "wire"):  # type: ignore[no-untyped-def]
    component = GENERATORS[0].build(canvas, WIRE).unwrap()
    return component if name == "wire" else component


# Constructs outside the IR subset are errors with the shared codes


def test_regular_klayout_instances_keep_array_axes_and_name() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        placed = top.kdb_cell.insert(
            kdb.CellInstArray(
                child.cell_index(),
                kdb.Trans(1, True, 100, 200),
                kdb.Vector(-2000, 0),
                kdb.Vector(0, 3000),
                3,
                2,
            )
        )
        placed.set_property(0, "repeated")
        return "top"

    layout = convert_upstream(build).unwrap()
    (instance,) = layout.cells[1].instances
    assert instance.name == "repeated"
    assert instance.transform == Transform(rotation=90, mirror=True, translation=(100, 200))
    assert instance.array == InstanceArray(
        columns=3, rows=2, column_step=(-2000, 0), row_step=(0, 3000)
    )


def test_nonrectangular_array_instances_are_unsupported() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        vector = kdb.Vector
        top.kdb_cell.insert(
            kdb.CellInstArray(
                child.cell_index(), kdb.Trans(), vector(2000, 2000), vector(0, 2000), 3, 2
            )
        )
        return "top"

    result = convert_upstream(build)
    assert result.value is None
    assert codes(result) == ["layout.unsupported.array_instance"]
    assert "Manhattan vector" in result.diagnostics[0].message
    assert result.diagnostics[0].location == ("cells", "top", "instances", 0)


def test_rotated_and_magnified_instances_are_unsupported() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        top.add_ref(child, name="turned").drotate(45)
        top.kdb_cell.insert(
            kdb.CellInstArray(child.cell_index(), kdb.ICplxTrans(2.0, 0.0, False, 0, 0))
        )
        top.kdb_cell.insert(
            kdb.CellInstArray(child.cell_index(), kdb.ICplxTrans(1.0, 30.0, True, 5, 5))
        )
        return "top"

    result = convert_upstream(build)
    assert result.value is None
    assert sorted(codes(result)) == [
        "geometry.unsupported.magnification",
        "geometry.unsupported.rotation",
        "geometry.unsupported.rotation",
    ]


def test_complex_transforms_that_are_exactly_manhattan_are_accepted() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        # a complex transformation whose angle and magnification differ from 90 and 1 by float noise
        noisy = kdb.ICplxTrans(1.0 + 1e-12, 90.0 + 1e-9, True, 100, -50)
        placed = top.kdb_cell.insert(kdb.CellInstArray(child.cell_index(), noisy))
        placed.set_property(0, "noisy")
        return "top"

    layout = convert_upstream(build).unwrap()
    (instance,) = layout.cells[1].instances
    assert instance.transform == Transform(mirror=True, rotation=90, translation=(100, -50))


def test_geometry_on_a_layer_outside_the_table_is_an_error() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        top.kdb_cell.shapes(canvas.kcl.layer(99, 9)).insert(kdb.Box(0, 0, 10, 10))
        top.kdb_cell.shapes(canvas.kcl.layer(99, 9)).insert(kdb.Box(20, 0, 30, 10))
        canvas.polygon(top, [(0, 0), (10, 0), (0, 10)])
        return "top"

    result = convert_upstream(build)
    assert result.value is None
    (found,) = errors(result)
    assert found.code == BackendCode.LAYER_UNMAPPED
    assert "99/9" in found.message and "(2 shapes)" in found.message
    assert found.location == ("cells", "top", "shapes", "99/9")


def test_shapes_the_ir_cannot_hold_are_errors() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        shapes = top.kdb_cell.shapes(canvas.layer)
        ring = kdb.Polygon(kdb.Box(0, 0, 100, 100))
        ring.insert_hole(kdb.Box(20, 20, 60, 60))
        shapes.insert(ring)
        shapes.insert(kdb.Text("label", 0, 0))
        shapes.insert(kdb.Path([kdb.Point(0, 0), kdb.Point(1000, 0)], 100, 0, 0, True))
        shapes.insert(kdb.Path([kdb.Point(0, 0), kdb.Point(1000, 0)], 100, 30, 0, False))
        shapes.insert(kdb.Box(0, 0, 0, 50))
        return "top"

    result = convert_upstream(build)
    assert result.value is None
    assert sorted(codes(result)) == [
        "geometry.unsupported.curve",
        "geometry.unsupported.path_end",
        "geometry.unsupported.polygon_holes",
        "geometry.unsupported.shape",
        BackendCode.DEGENERATE_SHAPE,
    ]


def test_paths_with_supported_ends_keep_their_form() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        shapes = top.kdb_cell.shapes(canvas.layer)
        shapes.insert(kdb.Path([kdb.Point(0, 0), kdb.Point(1000, 0)], 100, 0, 0))
        shapes.insert(kdb.Path([kdb.Point(0, 500), kdb.Point(1000, 500)], 100, 50, 50))
        return "top"

    layout = convert_upstream(build).unwrap()
    assert layout.cells[0].shapes["WG"] == (
        Path(points=((0, 0), (1000, 0)), width=100, end="flush"),
        Path(points=((0, 500), (1000, 500)), width=100, end="square"),
    )


def test_diagonal_paths_fail_layout_validation() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        top.kdb_cell.shapes(canvas.layer).insert(
            kdb.Path([kdb.Point(0, 0), kdb.Point(1000, 1000)], 100)
        )
        return "top"

    result = convert_upstream(build)
    assert result.value is None and codes(result) == ["geometry.unsupported.path_segment"]


def test_unsupported_ports_are_errors() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        top.add_port(name="slanted", center=(0, 0), width=0.5, orientation=45, layer=canvas.layer)
        top.add_port(
            name="shifted", center=(0.0004, 1), width=0.5, orientation=0, layer=canvas.layer
        )
        top.add_port(
            name="tagged",
            center=(1, 1),
            width=0.5,
            orientation=0,
            layer=canvas.layer,
            port_type="placement",
        )
        top.add_port(
            name="stray", center=(2, 1), width=0.5, orientation=0, layer=canvas.kcl.layer(99, 9)
        )
        return "top"

    result = convert_upstream(build)
    assert result.value is None
    by_name = {d.location: d for d in errors(result)}
    assert codes(result) == [
        "geometry.unsupported.rotation",
        BackendCode.PORT_UNSUPPORTED,
        BackendCode.PORT_UNSUPPORTED,
        BackendCode.PORT_UNSUPPORTED,
    ]
    assert by_name[("cells", "top", "ports", 0)].code == "geometry.unsupported.rotation"
    assert "off the grid" in by_name[("cells", "top", "ports", 1)].message
    assert "'placement'" in by_name[("cells", "top", "ports", 2)].message
    assert "99/9" in by_name[("cells", "top", "ports", 3)].message


def test_a_ports_orientation_may_carry_float_noise() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        top.add_port(
            name="up", center=(0, 0), width=0.5, orientation=90.0000000001, layer=canvas.layer
        )
        return "top"

    layout = convert_upstream(build).unwrap()
    assert [(p.name, p.orientation) for p in layout.cells[0].ports] == [("up", 90)]


def test_the_database_unit_must_match_the_declared_grid() -> None:
    ly = kdb.Layout()
    ly.dbu = 0.002
    ly.create_cell("top")
    result = layout_from_klayout(
        ly,
        layout_id=EntityId("layout", "x"),
        grid=Grid(dbu_pm=1000),
        layers=TABLE,
        top=None,
        ports={},
    )
    assert result.value is None and [d.code for d in result.diagnostics] == [
        BackendCode.GRID_MISMATCH
    ]


# Names: normalised explicitly, never silently


def test_names_outside_the_ir_pattern_are_rewritten_and_recorded() -> None:
    assert clean_name("a b/c#d") == "a_b_c_d"
    assert (
        clean_name("-lead") == "_-lead"
        and clean_name("") == "_"
        and clean_name("ok.$-1") == "ok.$-1"
    )

    def build(canvas: Canvas) -> str:
        child = canvas.component("wire 1/2")
        canvas.polygon(child, [(0, 0), (10, 0), (0, 10)])
        top = canvas.component("top level")
        top.add_ref(child, name="first arm").trans = kdb.Trans(1, False, 0, 0)
        return "top level"

    result = convert_upstream(build)
    layout = result.unwrap()
    assert layout.top == "top_level"
    assert [c.name for c in layout.cells] == ["top_level", "wire_1_2"]
    assert [i.name for i in layout.cells[0].instances] == ["first_arm"]
    assert layout.cells[0].instances[0].cell == "wire_1_2"
    assert layout.metadata == {
        "name.cell.top_level": "top level",
        "name.cell.wire_1_2": "wire 1/2",
        "name.instance.top_level.first_arm": "first arm",
    }
    notes = [d for d in result.diagnostics if d.code == BackendCode.NAME_SANITIZED]
    assert len(notes) == 3 and all(d.severity == "info" for d in notes)
    assert validate_layout(layout) == []


def test_names_that_collide_after_cleaning_are_an_error() -> None:
    def build(canvas: Canvas) -> str:
        top = canvas.component("top")
        for name in ("x y", "x_y"):
            canvas.polygon(canvas.component(name), [(0, 0), (10, 0), (0, 10)])
        return top.name

    result = convert_upstream(build)
    assert result.value is None and codes(result) == [BackendCode.NAME_COLLISION]


def test_unnamed_instances_get_deterministic_names() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        for rotation, x in ((2, 5000), (0, 0), (1, 100)):
            top.kdb_cell.insert(
                kdb.CellInstArray(child.cell_index(), kdb.Trans(rotation, False, x, 0))
            )
        return "top"

    result = convert_upstream(build)
    layout = result.unwrap()
    assert {i.name: i.transform.rotation for i in layout.cells[1].instances} == {
        "straight_1": 0,
        "straight_2": 90,
        "straight_3": 180,
    }
    assert [d.code for d in result.diagnostics] == [BackendCode.INSTANCE_NAMES_GENERATED]


def test_duplicate_instance_names_are_an_error() -> None:
    def build(canvas: Canvas) -> str:
        child = straight(canvas)
        top = canvas.component("top")
        for x in (0, 3000):
            top.add_ref(child, name="same").trans = kdb.Trans(0, False, x, 0)
        return "top"

    assert codes(convert_upstream(build)) == [BackendCode.NAME_COLLISION]


# Foreign GDS


def foreign(build) -> bytes:  # type: ignore[no-untyped-def]
    ly = kdb.Layout()
    ly.dbu = 0.001
    build(ly, ly.layer(1, 0))
    return write_bytes(ly)


def import_foreign(data: bytes, **kwargs) -> Result[Layout]:  # type: ignore[no-untyped-def]
    request = ImportRequest(layers=TABLE, layout_id=EntityId("layout", "foreign"), **kwargs)
    return BACKEND.import_gds(data, request)


_AREF_FIELDS = struct.Struct(">HBBhhHBB6i")


def foreign_array(counts: tuple[int, int], xy: tuple[int, ...]) -> bytes:
    def build(ly: kdb.Layout, wg: int) -> None:
        child, top = ly.create_cell("child"), ly.create_cell("top")
        child.shapes(wg).insert(kdb.Box(0, 0, 100, 100))
        top.insert(
            kdb.CellInstArray(
                child.cell_index(), kdb.Trans(), kdb.Vector(200, 0), kdb.Vector(0, 300), 2, 3
            )
        )

    def records(counts: tuple[int, int], xy: tuple[int, ...]) -> bytes:
        return _AREF_FIELDS.pack(8, 0x13, 2, *counts, 28, 0x10, 3, *xy)

    data = foreign(build)
    original = records((2, 3), (0, 0, 400, 0, 0, 900))
    assert data.count(original) == 1
    return data.replace(original, records(counts, xy))


def test_foreign_array_normalizes_reversed_and_transposed_wire_indices() -> None:
    result = import_foreign(foreign_array((2, 3), (1000, 1000, 1000, 600, 100, 1000)))
    (instance,) = result.unwrap().cells[1].instances
    assert instance.name == "child_1"
    assert instance.transform == Transform(translation=(400, 800))
    assert instance.array == InstanceArray(
        columns=3, rows=2, column_step=(300, 0), row_step=(0, 200)
    )
    assert "layout.backend.array_normalized" in [finding.code for finding in result.diagnostics]


def test_foreign_singleton_array_is_a_single_instance() -> None:
    # Unused steps do not place any geometry.
    result = import_foreign(foreign_array((1, 1), (100, 100, 101, 103, 200, 999)))
    (instance,) = result.unwrap().cells[1].instances
    assert instance.array is None
    assert instance.transform.translation == (100, 100)


@pytest.mark.parametrize(
    ("counts", "xy", "detail"),
    [
        ((2, 3), (0, 0, 401, 0, 0, 900), "integer number"),
        ((2, 3), (0, 0, 0, 0, 0, 900), "nonzero Manhattan vector"),
        ((2, 3), (0, 0, 400, 400, 0, 900), "nonzero Manhattan vector"),
        ((2, 3), (0, 0, 400, 0, 900, 0), "perpendicular"),
        ((0, 3), (0, 0, 0, 0, 0, 900), "counts must be positive"),
        ((2, 1), (-(2**30), 0, 2**30, 0, -(2**30), 0), "signed 32-bit"),
    ],
)
def test_foreign_arrays_klayout_could_change_are_refused(
    counts: tuple[int, int], xy: tuple[int, ...], detail: str
) -> None:
    result = import_foreign(foreign_array(counts, xy))
    assert result.value is None
    (finding,) = errors(result)
    assert finding.code == "layout.unsupported.array_instance"
    assert finding.location == ("cells", "top", "instances", 0)
    assert detail in finding.message


def test_foreign_gds_with_unsupported_constructs_is_refused() -> None:
    def build(ly: kdb.Layout, wg: int) -> None:
        child, top = ly.create_cell("child"), ly.create_cell("top")
        child.shapes(wg).insert(kdb.Box(0, 0, 100, 100))
        ring = kdb.Polygon(kdb.Box(0, 0, 100, 100))
        ring.insert_hole(kdb.Box(20, 20, 60, 60))
        top.shapes(wg).insert(ring)
        top.shapes(ly.layer(2, 0)).insert(kdb.Text("label", 0, 0))
        top.insert(
            kdb.CellInstArray(
                child.cell_index(), kdb.Trans(), kdb.Vector(200, 0), kdb.Vector(0, 200), 2, 2
            )
        )

    result = import_foreign(foreign(build))
    assert result.value is None
    assert sorted(codes(result)) == [
        "geometry.unsupported.polygon_holes",  # GDS stores the hole as a zero-width keyhole
        "geometry.unsupported.shape",
    ]


def test_foreign_gds_without_instance_names_or_ports_imports_with_notes() -> None:
    def build(ly: kdb.Layout, wg: int) -> None:
        child, top = ly.create_cell("child"), ly.create_cell("top")
        child.shapes(wg).insert(kdb.Box(0, 0, 100, 100))
        top.insert(kdb.CellInstArray(child.cell_index(), kdb.Trans(1, True, 10, 20)))

    result = import_foreign(foreign(build))
    layout = result.unwrap()
    assert layout.top == "top" and layout.grid == Grid(dbu_pm=1000)
    (instance,) = layout.cells[1].instances
    assert (instance.name, instance.cell) == ("child_1", "child")
    assert instance.transform == Transform(mirror=True, rotation=90, translation=(10, 20))
    assert layout.cells[0].shapes == {"WG": (Rectangle(lo=(0, 0), hi=(100, 100)),)}
    assert [d.code for d in result.diagnostics] == [
        BackendCode.NO_PORTS,
        BackendCode.INSTANCE_NAMES_GENERATED,
    ]


def test_foreign_gds_with_several_top_cells_needs_a_choice() -> None:
    def build(ly: kdb.Layout, wg: int) -> None:
        for name in ("a", "b"):
            ly.create_cell(name).shapes(wg).insert(kdb.Box(0, 0, 100, 100))

    data = foreign(build)
    assert codes(import_foreign(data)) == [BackendCode.TOP_AMBIGUOUS]
    chosen = import_foreign(data, top="b").unwrap()
    assert chosen.top == "b" and [c.name for c in chosen.cells] == ["a", "b"]  # nothing dropped
    assert codes(import_foreign(data, top="zz")) == ["layout.top.unknown"]


def test_references_to_cells_the_file_does_not_define_are_refused() -> None:
    from waffle.contracts.layout.examples import nested_layout

    layout = nested_layout()
    without_leaf = layout.model_copy(
        update={"cells": tuple(c for c in layout.cells if c.name != "leaf")}
    )
    result = import_foreign(gds_bytes(without_leaf))
    assert result.value is None
    assert codes(result) == ["layout.unsupported.external_reference"]
    assert "'leaf'" in errors(result)[0].message


def test_files_that_are_not_gds_are_refused() -> None:
    result = import_foreign(b"definitely not a gds stream")
    assert result.value is None and codes(result) == ["gds.stream.invalid"]


def test_polygons_klayout_would_split_on_export_are_refused() -> None:
    ly = kdb.Layout()
    big = ly.create_cell("big")
    ring = [
        kdb.Point(
            round(1e8 * math.cos(a / 8001 * math.tau)), round(1e8 * math.sin(a / 8001 * math.tau))
        )
        for a in range(8001)
    ]
    big.shapes(ly.layer(1, 0)).insert(kdb.Polygon(ring))
    ly.create_cell("small").shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 10, 10))
    (found,) = oversized_polygons(ly)
    assert found.code == BackendCode.EXPORT_FAILED and found.location == ("cells", "big")
    assert "8001 vertices" in found.message
