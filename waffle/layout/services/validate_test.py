from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import LAYERS, LEAF, nested_layout
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout, Port
from waffle.layout.services.validate import find_cycles, validate_layout

BASE = nested_layout()


def codes(diagnostics: list[Diagnostic]) -> list[str]:
    return sorted(d.code for d in diagnostics)


def variant(**changes: object) -> Layout:
    return BASE.model_copy(update=changes)


def with_cell(cell: Cell, name: str = "leaf") -> Layout:
    return variant(cells=tuple(cell if c.name == name else c for c in BASE.cells))


def port(name: str = "p", layer: str = "WG", center: tuple[int, int] = (0, 0)) -> Port:
    return Port(
        name=name, center=center, width=2, orientation=0, layer=layer, domain=Domain.OPTICAL
    )


def test_well_formed_layout_has_no_diagnostics() -> None:
    assert validate_layout(BASE) == []


def test_duplicate_layers() -> None:
    twice = (*LAYERS, LayerSpec(name="WG", layer=9, datatype=0))
    assert codes(validate_layout(variant(layers=twice))) == ["layout.layer.duplicate_name"]
    same_pair = (*LAYERS, LayerSpec(name="WG2", layer=1, datatype=0))
    assert codes(validate_layout(variant(layers=same_pair))) == ["layout.layer.duplicate_pair"]


def test_duplicate_cell() -> None:
    found = validate_layout(variant(cells=(*BASE.cells, LEAF)))
    assert codes(found) == ["layout.cell.duplicate"]
    assert found[0].location == ("cells", "leaf")


def test_unknown_top() -> None:
    assert codes(validate_layout(variant(top="nope"))) == ["layout.top.unknown"]


def test_duplicate_instance_name_and_unknown_cell() -> None:
    dup = Cell(
        name="leaf",
        instances=(Instance(name="a", cell="mid"), Instance(name="a", cell="ghost")),
    )
    found = validate_layout(with_cell(dup))
    assert codes(found) == [
        "layout.hierarchy.cycle",
        "layout.instance.duplicate",
        "layout.instance.unknown_cell",
    ]
    unknown = next(d for d in found if d.code == "layout.instance.unknown_cell")
    assert unknown.location == ("cells", "leaf", "instances", "a")


def test_duplicate_port_name() -> None:
    found = validate_layout(with_cell(Cell(name="leaf", ports=(port("p"), port("p")))))
    assert codes(found) == ["layout.port.duplicate"]


def test_shapes_and_ports_on_layers_missing_from_the_table() -> None:
    bad = Cell(
        name="leaf",
        shapes={"NOPE": (Rectangle(lo=(0, 0), hi=(1, 1)),)},
        ports=(port(layer="ALSO_NOPE"),),
    )
    assert codes(validate_layout(with_cell(bad))) == [
        "layout.port.unknown_layer",
        "layout.shape.unknown_layer",
    ]


def test_recursive_hierarchy_is_reported_with_its_path() -> None:
    loop = Cell(name="leaf", instances=(Instance(name="up", cell="top"),))
    found = [d for d in validate_layout(with_cell(loop)) if d.code == "layout.hierarchy.cycle"]
    assert len(found) == 1
    assert found[0].message == "recursive hierarchy: leaf -> top -> mid -> leaf"
    selfie = Cell(name="leaf", instances=(Instance(name="me", cell="leaf"),))
    assert codes(validate_layout(with_cell(selfie))) == ["layout.hierarchy.cycle"]
    assert find_cycles({c.name: c for c in BASE.cells}) == []


def test_invalid_shapes_are_reported_with_locations() -> None:
    bad = Cell(
        name="leaf",
        shapes={
            "WG": (
                Path(points=((0, 0), (5, 5)), width=2),
                Polygon(points=((0, 0), (4, 4), (4, 0), (0, 3))),
            )
        },
    )
    found = validate_layout(with_cell(bad))
    assert codes(found) == [
        "geometry.polygon.self_intersecting",
        "geometry.unsupported.path_segment",
    ]
    assert all(d.location[:3] == ("cells", "leaf", "shapes") for d in found)
    assert codes(validate_layout(with_cell(bad), check_simple=False)) == [
        "geometry.unsupported.path_segment"
    ]


def test_points_must_be_on_the_manufacturing_grid() -> None:
    coarse = variant(grid=Grid(dbu_pm=1000, manufacturing=2))
    found = validate_layout(coarse)
    assert codes(found).count("layout.grid.off_manufacturing") > 0
    shifted = Cell(
        name="leaf",
        shapes={"WG": (Rectangle(lo=(0, 0), hi=(4, 2)),)},
        instances=(Instance(name="i", cell="mid", transform=Transform(translation=(3, 0))),),
        ports=(port(center=(1, 0)),),
    )
    found = validate_layout(
        Layout(
            id=BASE.id,
            grid=Grid(dbu_pm=1000, manufacturing=2),
            top="leaf",
            layers=LAYERS,
            cells=(shifted, *[c for c in BASE.cells if c.name == "mid"]),
        )
    )
    assert [
        d.location[2:]
        for d in found
        if d.code == "layout.grid.off_manufacturing" and d.location[:2] == ("cells", "leaf")
    ] == [
        ("instances", "i"),
        ("ports", "p"),
    ]
