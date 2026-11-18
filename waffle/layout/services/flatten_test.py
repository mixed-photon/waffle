from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.geometry.shapes import BBox
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Cell, Instance
from waffle.layout.services.flatten import flatten
from waffle.layout.services.physical import to_physical


def test_three_level_hierarchy_flattens_to_hand_computed_geometry() -> None:
    # leaf -(mirror,+10,0)-> mid -(rot 90,+100,0 | mirror+rot 180,+0,50)-> top
    flat = flatten(nested_layout()).unwrap()
    assert flat.cell == "top"
    assert {layer: [p.points for p in polys] for layer, polys in flat.polygons.items()} == {
        "M1": [
            ((-20, 50), (0, 50), (0, 55), (-20, 55)),
            ((95, 0), (100, 0), (100, 20), (95, 20)),
        ],
        "WG": [
            ((-14, 50), (-10, 48), (-10, 50)),
            ((100, 10), (102, 10), (100, 14)),
        ],
    }
    assert [(q.path, q.port.center, q.port.orientation) for q in flat.ports] == [
        ("in", (0, 0), 180),
        ("m1/e1", (98, 20), 90),
        ("m1/l1/o1", (101, 10), 270),
        ("m1/l1/o2", (100, 12), 180),
        ("m2/e1", (-20, 52), 180),
        ("m2/l1/o1", (-10, 49), 0),
        ("m2/l1/o2", (-12, 50), 90),
    ]
    assert flat.bounding_box() == BBox((-20, 0), (102, 55))


def test_port_identity_survives_flattening() -> None:
    layout = nested_layout()
    flat = flatten(layout).unwrap()
    by_path = {q.path: q.port for q in flat.ports}
    assert by_path["in"].name == "in"  # top-level ports keep their names
    leaf_o1 = layout.cells[0].ports[0]
    for path in ("m1/l1/o1", "m2/l1/o1"):
        port = by_path[path]
        assert (port.name, port.layer, port.domain, port.width) == (
            leaf_o1.name,
            leaf_o1.layer,
            leaf_o1.domain,
            leaf_o1.width,
        )
    assert by_path["m1/e1"].domain is Domain.ELECTRICAL


def test_flatten_a_sub_cell_uses_its_own_frame() -> None:
    flat = flatten(nested_layout(), "mid").unwrap()
    assert [p.points for p in flat.polygons["WG"]] == [((10, -2), (14, 0), (10, 0))]
    assert [q.path for q in flat.ports] == ["e1", "l1/o1", "l1/o2"]
    assert flatten(nested_layout(), "ghost").diagnostics[0].code == "layout.cell.unknown"


def test_four_level_hierarchy_composes_transforms_outermost_last() -> None:
    layout = nested_layout()
    wrapper = Cell(
        name="wrap",
        instances=(
            Instance(name="t", cell="top", transform=layout.cells[2].instances[0].transform),
        ),
    )
    deep = layout.model_copy(update={"top": "wrap", "cells": (*layout.cells, wrapper)})
    flat = flatten(deep).unwrap()
    base = {q.path: q.port for q in flatten(layout).unwrap().ports}
    outer = wrapper.instances[0].transform
    for q in flat.ports:
        assert q.path.startswith("t/")
        assert q.port == base[q.path[2:]].transformed(outer)


def test_empty_cell_flattens_to_nothing() -> None:
    layout = nested_layout().model_copy(update={"top": "leaf"})
    empty = layout.model_copy(update={"cells": (Cell(name="leaf"),)})
    flat = flatten(empty).unwrap()
    assert (flat.polygons, flat.ports, flat.bounding_box()) == ({}, (), None)


def test_invalid_layouts_do_not_flatten() -> None:
    layout = nested_layout().model_copy(update={"top": "ghost"})
    result = flatten(layout)
    assert result.value is None
    assert [d.code for d in result.diagnostics] == ["layout.top.unknown", "layout.cell.unknown"]


def test_physical_units_are_exact_for_on_grid_values() -> None:
    layout = nested_layout()
    physical = to_physical(flatten(layout).unwrap(), layout.grid)
    assert physical.unit == "um"
    assert physical.polygons["WG"][1] == ((0.1, 0.01), (0.102, 0.01), (0.1, 0.014))
    port = next(p for p in physical.ports if p.path == "m1/l1/o1")
    assert (port.center, port.width, port.orientation, port.layer) == (
        (0.101, 0.01),
        0.002,
        270,
        "WG",
    )
    in_nm = to_physical(flatten(layout).unwrap(), layout.grid, "nm")
    assert in_nm.polygons["WG"][1] == ((100.0, 10.0), (102.0, 10.0), (100.0, 14.0))
