from __future__ import annotations

import random
from itertools import product

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import load_record, to_canonical_json
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.geometry.transform import Orientation, Transform
from waffle.contracts.layout.examples import LAYERS, LEAF, MID, nested_layout
from waffle.contracts.layout.layout import Cell, Instance, Layout, Port
from waffle.layout.services.cells import instance_ports, transform_cell
from waffle.layout.services.flatten import flatten

ORIENTATIONS: list[tuple[bool, Orientation]] = list(product((False, True), (0, 90, 180, 270)))

STRAIGHT = Cell(
    name="straight",
    shapes={"WG": (Polygon(points=((0, -1), (10, -1), (10, 1), (0, 1))),)},
    ports=(
        Port(name="o1", center=(0, 0), width=2, orientation=180, layer="WG", domain=Domain.OPTICAL),
        Port(name="o2", center=(10, 0), width=2, orientation=0, layer="WG", domain=Domain.OPTICAL),
    ),
)


def unit_step(angle: int) -> tuple[int, int]:
    return {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle]


def test_transform_cell_moves_everything_and_keeps_names() -> None:
    t = Transform(mirror=True, rotation=90, translation=(5, 5))
    moved = transform_cell(MID, t)
    assert moved.name == "mid"
    assert [p.name for p in moved.ports] == ["e1"]
    assert moved.ports[0].center == t.apply((20, 2))
    assert moved.instances[0].name == "l1"
    assert moved.instances[0].transform == t.compose(MID.instances[0].transform)
    assert transform_cell(moved, t.inverse()) == MID


def test_transforming_a_cell_is_the_same_as_transforming_its_flattening() -> None:
    base = nested_layout()
    t = Transform(mirror=True, rotation=270, translation=(-7, 13))
    top = next(c for c in base.cells if c.name == "top")
    moved = base.model_copy(
        update={"cells": tuple(transform_cell(c, t) if c is top else c for c in base.cells)}
    )
    before, after = flatten(base).unwrap(), flatten(moved).unwrap()
    assert [(q.path, q.port) for q in after.ports] == sorted(
        ((q.path, q.port.transformed(t)) for q in before.ports), key=lambda x: x[0]
    )
    expected = {
        layer: sorted((p.transformed(t) for p in polys), key=lambda p: p.points)
        for layer, polys in before.polygons.items()
    }
    assert {k: list(v) for k, v in after.polygons.items()} == expected


def test_port_identity_survives_instantiation_in_all_eight_orientations() -> None:
    rng = random.Random(7)
    for mirror, rotation in ORIENTATIONS:
        t = Transform(
            mirror=mirror,
            rotation=rotation,
            translation=(rng.randint(-99, 99), rng.randint(-99, 99)),
        )
        layout = Layout(
            id=EntityId("layout", "pair"),
            grid=Grid(dbu_pm=1000),
            top="parent",
            layers=LAYERS,
            cells=(
                Cell(name="parent", instances=(Instance(name="s", cell="straight", transform=t),)),
                STRAIGHT,
            ),
        )
        ports = {
            q.path: q.port for q in instance_ports(layout, layout.cells[0].instances[0]).unwrap()
        }
        assert set(ports) == {"s/o1", "s/o2"}
        o1, o2 = ports["s/o1"], ports["s/o2"]
        assert (o1.name, o2.name) == ("o1", "o2")
        assert (o1.layer, o1.domain, o1.width) == ("WG", Domain.OPTICAL, 2)
        # rigidity: still 10 apart, still facing away from each other along the line joining them
        step = unit_step(o2.orientation)
        assert (o2.center[0] - o1.center[0], o2.center[1] - o1.center[1]) == (
            10 * step[0],
            10 * step[1],
        )
        assert (o1.orientation - o2.orientation) % 360 == 180
        # and the same through flattening
        flat = {q.path: q.port for q in flatten(layout).unwrap().ports}
        assert flat == ports


def test_mated_instances_stay_connected_under_a_common_transform() -> None:
    a = Instance(name="a", cell="straight")
    b = Instance(name="b", cell="straight", transform=Transform(mirror=True, translation=(10, 0)))
    for mirror, rotation in ORIENTATIONS:
        common = Transform(mirror=mirror, rotation=rotation, translation=(31, -17))
        top = Cell(name="top", instances=(a, b))
        layout = Layout(
            id=EntityId("layout", "chain"),
            grid=Grid(dbu_pm=1000),
            top="top",
            layers=LAYERS,
            cells=(top, STRAIGHT),
        )
        moved = layout.model_copy(update={"cells": (transform_cell(top, common), STRAIGHT)})
        for lay in (layout, moved):
            ports = {q.path: q.port for q in flatten(lay).unwrap().ports}
            assert ports["a/o2"].center == ports["b/o1"].center
            assert (ports["a/o2"].orientation - ports["b/o1"].orientation) % 360 == 180


def test_instance_ports_of_an_unknown_cell_is_a_diagnostic() -> None:
    result = instance_ports(nested_layout(), Instance(name="x", cell="ghost"))
    assert [d.code for d in result.diagnostics] == ["layout.instance.unknown_cell"]


def test_leaf_ports_are_unchanged_by_the_identity_instance() -> None:
    layout = nested_layout()
    ports = instance_ports(layout, Instance(name="x", cell="leaf")).unwrap()
    assert [(q.path, q.port) for q in ports] == [(f"x/{p.name}", p) for p in LEAF.ports]
    assert load_record(to_canonical_json(layout), Layout).unwrap() == layout
