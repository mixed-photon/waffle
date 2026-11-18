from __future__ import annotations

import json

import pytest

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import LEAF, MID, TOP, nested_layout
from waffle.contracts.layout.layout import Cell, Instance, Layout, Port


def test_hierarchical_layout_round_trips_through_serialization() -> None:
    original = nested_layout()
    text = to_canonical_json(original)
    loaded = load_record(text, Layout).unwrap()
    assert loaded == original
    assert revision_of(loaded) == revision_of(original)
    assert to_canonical_json(loaded) == text


def test_round_trip_covers_every_shape_type_and_transform() -> None:
    cell = Cell(
        name="all",
        shapes={
            "WG": (
                Path(points=((0, 0), (10, 0), (10, 10)), width=2, end="square"),
                Polygon(points=((0, 0), (4, 0), (0, 2))),
                Rectangle(lo=(-5, -5), hi=(5, 5)),
            )
        },
        instances=(
            Instance(
                name="i",
                cell="all2",
                transform=Transform(mirror=True, rotation=270, translation=(-3, 4)),
            ),
        ),
        ports=(
            Port(
                name="e",
                center=(-1, 2),
                width=6,
                orientation=90,
                layer="WG",
                domain=Domain.ELECTRICAL,
            ),
        ),
    )
    layout = Layout(
        id=EntityId("layout", "x"), grid=Grid(dbu_pm=500, manufacturing=2), top="all", cells=(cell,)
    )
    loaded = load_record(json.loads(to_canonical_json(layout)), Layout).unwrap()
    assert loaded == layout
    assert revision_of(loaded) == revision_of(layout)


def test_serialization_is_compact_and_orders_are_normalized() -> None:
    text = to_canonical_json(nested_layout())
    assert '"points":[[0,0],[4,0],[0,2]]' in text
    shuffled = Layout(
        id=EntityId("layout", "nested"),
        grid=Grid(dbu_pm=1000),
        top="top",
        layers=tuple(reversed(nested_layout().layers)),
        cells=(LEAF, TOP, MID),
    )
    assert to_canonical_json(shuffled) == text
    assert revision_of(shuffled) == revision_of(nested_layout())
    assert [c.name for c in nested_layout().cells] == ["leaf", "mid", "top"]
    assert [p.name for p in LEAF.ports] == ["o1", "o2"]


def test_shape_order_within_a_layer_is_not_significant() -> None:
    a, b = Rectangle(lo=(0, 0), hi=(1, 1)), Rectangle(lo=(5, 5), hi=(6, 6))
    assert Cell(name="c", shapes={"WG": (a, b)}) == Cell(name="c", shapes={"WG": (b, a)})


def test_any_change_changes_the_revision() -> None:
    changed = nested_layout().model_copy(update={"metadata": {"note": "x"}})
    assert revision_of(changed) != revision_of(nested_layout())


def test_malformed_payloads_fail_with_diagnostics() -> None:
    data = json.loads(to_canonical_json(nested_layout()))
    data["cells"][0]["instances"] = [{"name": "a", "cell": "leaf", "transform": {"rotation": 45}}]
    result = load_record(data, Layout)
    assert result.value is None
    assert {d.code for d in result.diagnostics} == {"record.invalid"}
    assert any("rotation" in d.location for d in result.diagnostics)

    data = json.loads(to_canonical_json(nested_layout()))
    data["cells"][0]["shapes"]["WG"] = [{"type": "circle", "r": 5}]
    assert load_record(data, Layout).value is None
    data = json.loads(to_canonical_json(nested_layout()))
    data["cells"][0]["instances"] = [{"name": "a", "cell": "leaf", "array": [2, 2]}]
    assert load_record(data, Layout).value is None


def test_names_cannot_contain_the_path_separator() -> None:
    with pytest.raises(ValueError):
        Cell(name="a/b")
    with pytest.raises(ValueError):
        Instance(name="a/b", cell="leaf")
    with pytest.raises(ValueError):
        Port(name="a/b", center=(0, 0), width=1, orientation=0, layer="WG", domain=Domain.OPTICAL)


def test_port_keeps_its_identity_when_transformed() -> None:
    port = LEAF.ports[0]
    moved = port.transformed(Transform(mirror=True, rotation=90, translation=(5, 5)))
    assert (moved.name, moved.layer, moved.domain, moved.width) == ("o1", "WG", Domain.OPTICAL, 2)
    # mirror (0,1)->(0,-1); rot90 -> (1,0); +(5,5); direction 180 -> (-1,0) -> (0,-1) = 270
    assert (moved.center, moved.orientation) == ((6, 5), 270)
