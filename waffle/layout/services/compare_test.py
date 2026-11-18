from __future__ import annotations

from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import LEAF, MID, TOP, nested_layout
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout
from waffle.layout.services.compare import CompareMode, compare_layouts
from waffle.layout.services.regrid import regrid

BASE = nested_layout()
FLAT = CompareMode.FLAT


def codes(
    layouts: tuple[Layout, Layout], mode: CompareMode = CompareMode.HIERARCHICAL
) -> list[str]:
    return sorted(d.code for d in compare_layouts(*layouts, mode))


def replace_cell(layout: Layout, cell: Cell) -> Layout:
    return layout.model_copy(
        update={"cells": tuple(cell if c.name == cell.name else c for c in layout.cells)}
    )


def test_identical_layouts_are_equal_in_both_modes() -> None:
    for mode in CompareMode:
        assert compare_layouts(BASE, BASE, mode) == []


def test_ids_metadata_and_storage_order_are_ignored() -> None:
    other = Layout(
        id=EntityId("layout", "different"),
        metadata={"note": "x"},
        grid=Grid(dbu_pm=1000, manufacturing=1),
        top="top",
        layers=tuple(reversed(BASE.layers)),
        cells=(LEAF, MID, TOP),
    )
    for mode in CompareMode:
        assert compare_layouts(BASE, other, mode) == []


def test_a_rectangle_equals_its_polygon_and_a_path_its_outline() -> None:
    as_polygon = Cell(
        name="mid",
        shapes={"M1": (Polygon(points=((0, 0), (20, 0), (20, 5), (0, 5))),)},
        instances=MID.instances,
        ports=MID.ports,
    )
    assert compare_layouts(BASE, replace_cell(BASE, as_polygon)) == []
    a = replace_cell(
        BASE, Cell(name="leaf", shapes={"WG": (Path(points=((0, 0), (10, 0)), width=4),)})
    )
    b = replace_cell(BASE, Cell(name="leaf", shapes={"WG": (Rectangle(lo=(0, -2), hi=(10, 2)),)}))
    assert compare_layouts(a, b) == []


def test_hierarchical_differences_are_reported_with_locations() -> None:
    changed_shape = replace_cell(
        BASE,
        Cell(
            name="leaf",
            shapes={"WG": (Polygon(points=((0, 0), (5, 0), (0, 2))),)},
            ports=LEAF.ports,
        ),
    )
    found = compare_layouts(BASE, changed_shape)
    assert [(d.code, d.location) for d in found] == [
        ("layout.diff.shapes", ("cells", "leaf", "shapes", "WG"))
    ]

    moved = replace_cell(
        BASE,
        Cell(
            name="top",
            instances=(
                Instance(
                    name="m1", cell="mid", transform=Transform(rotation=270, translation=(100, 0))
                ),
                TOP.instances[1],
            ),
            ports=TOP.ports,
        ),
    )
    assert [(d.code, d.location) for d in compare_layouts(BASE, moved)] == [
        ("layout.diff.instance", ("cells", "top", "instances", "m1"))
    ]

    renamed_port = Cell(
        name="leaf",
        shapes=LEAF.shapes,
        ports=(LEAF.ports[0].model_copy(update={"name": "in1"}), LEAF.ports[1]),
    )
    assert codes((BASE, replace_cell(BASE, renamed_port))) == ["layout.diff.port"] * 2
    turned = Cell(
        name="leaf",
        shapes=LEAF.shapes,
        ports=(LEAF.ports[0].model_copy(update={"orientation": 90}), LEAF.ports[1]),
    )
    assert codes((BASE, replace_cell(BASE, turned))) == ["layout.diff.port"]


def test_structural_differences() -> None:
    extra = BASE.model_copy(update={"cells": (*BASE.cells, Cell(name="orphan"))})
    assert [(d.code, d.location) for d in compare_layouts(BASE, extra)] == [
        ("layout.diff.cell", ("cells", "orphan"))
    ]
    assert codes((BASE, BASE.model_copy(update={"top": "mid"}))) == ["layout.diff.top"]
    remapped = BASE.model_copy(
        update={"layers": (LayerSpec(name="WG", layer=2, datatype=0), BASE.layers[0])}
    )
    for mode in CompareMode:
        assert codes((BASE, remapped), mode) == ["layout.diff.layer"]


def test_different_database_units_are_reported_before_anything_else() -> None:
    finer = regrid(BASE, Grid(dbu_pm=500)).unwrap()
    assert codes((BASE, finer)) == ["layout.diff.grid"]


def test_flat_mode_ignores_cell_names_but_not_geometry_or_port_paths() -> None:
    renamed = Layout(
        id=BASE.id,
        grid=BASE.grid,
        top="top",
        layers=BASE.layers,
        cells=(
            TOP,
            MID.model_copy(update={"name": "mid2"}),
            LEAF,
        ),
    )
    swapped = renamed.model_copy(
        update={
            "cells": tuple(
                c.model_copy(
                    update={
                        "instances": tuple(
                            i.model_copy(update={"cell": "mid2"}) if i.cell == "mid" else i
                            for i in c.instances
                        )
                    }
                )
                for c in renamed.cells
            )
        }
    )
    assert codes((BASE, swapped)) == [
        "layout.diff.cell",
        "layout.diff.cell",
        "layout.diff.instance",
        "layout.diff.instance",
    ]
    assert compare_layouts(BASE, swapped, FLAT) == []

    nudged = replace_cell(
        swapped,
        Cell(
            name="leaf",
            shapes=LEAF.shapes,
            ports=(LEAF.ports[0], LEAF.ports[1].model_copy(update={"center": (2, 1)})),
        ),
    )
    flat = compare_layouts(BASE, nudged, FLAT)
    assert {d.code for d in flat} == {"layout.diff.port"}
    assert {d.location for d in flat} == {
        ("flat", "ports", "m1/l1/o2"),
        ("flat", "ports", "m2/l1/o2"),
    }


def test_flat_mode_detects_geometry_differences_across_the_hierarchy() -> None:
    bigger = replace_cell(
        BASE,
        Cell(
            name="leaf",
            shapes={"WG": (Polygon(points=((0, 0), (6, 0), (0, 2))),)},
            ports=LEAF.ports,
        ),
    )
    found = compare_layouts(BASE, bigger, FLAT)
    assert [(d.code, d.location) for d in found] == [
        ("layout.diff.shapes", ("flat", "shapes", "WG"))
    ]
    assert "2 polygons only in the first layout, 2 only in the second" in found[0].message


def test_invalid_layouts_are_reported_not_compared() -> None:
    broken = BASE.model_copy(update={"top": "ghost"})
    found = compare_layouts(BASE, broken)
    assert [(d.code, d.message) for d in found] == [
        ("layout.top.unknown", "second layout: top cell 'ghost' is not defined")
    ]
