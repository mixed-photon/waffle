from __future__ import annotations

import pytest

from waffle.contracts.component.definition import Domain
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import BBox, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import LEAF, MID, TOP, nested_layout
from waffle.contracts.layout.layout import (
    Cell,
    Instance,
    InstanceArray,
    LayerSpec,
    Layout,
    Port,
)
from waffle.geometry.services.shapes import polygon_intersects_box, to_polygon
from waffle.layout.services.flatten import flatten
from waffle.layout.services.window import extract_window


def test_window_matches_fully_expanded_reference_with_nested_signed_array() -> None:
    array = InstanceArray(columns=3, rows=2, column_step=(0, -6), row_step=(12, 0))
    mid = MID.model_copy(
        update={"instances": (MID.instances[0].model_copy(update={"array": array}),)}
    )
    layout = nested_layout().model_copy(update={"cells": (LEAF, mid, TOP)})
    flat = flatten(layout).unwrap()

    for box in (
        BBox((96, 9), (109, 14)),
        BBox((-26, 36), (-6, 50)),
        BBox((109, 24), (109, 24)),
    ):
        selected = extract_window(layout, box).unwrap()
        expected = {
            layer: tuple(poly for poly in polygons if polygon_intersects_box(poly, box))
            for layer, polygons in flat.polygons.items()
        }
        assert selected.polygons == {
            layer: polys for layer, polys in expected.items() if polys
        }
        assert selected.ports == tuple(
            port for port in flat.ports if box.contains(port.port.center)
        )
        assert selected.window == box

    member = extract_window(layout, BBox((106, 22), (109, 23))).unwrap()
    assert [port.path for port in member.ports] == ["m1/l1/1/1/o1"]
    assert member.ports[0].port.center == (107, 22)


def test_window_retains_whole_shape_when_geometry_crosses_the_edge() -> None:
    layout = nested_layout()
    selected = extract_window(layout, BBox((1, 1), (2, 2)), "mid").unwrap()
    expected = to_polygon(MID.shapes["M1"][0]).unwrap()
    assert selected.polygons == {"M1": (expected,)}
    assert not selected.ports


def test_window_visits_only_the_matching_member_of_large_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rect = Rectangle(lo=(0, 0), hi=(4, 4))
    leaf = Cell(
        name="leaf",
        shapes={"WG": (rect,)},
        ports=(
            Port(
                name="p",
                center=(2, 2),
                width=2,
                orientation=0,
                layer="WG",
                domain=Domain.OPTICAL,
            ),
        ),
    )
    layout = Layout(
        id="layout:array_window",
        grid=Grid(dbu_pm=1000),
        top="top",
        layers=(LayerSpec(name="WG", layer=1, datatype=0),),
        cells=(
            leaf,
            Cell(
                name="top",
                instances=(
                    Instance(
                        name="a",
                        cell="leaf",
                        array=InstanceArray(
                            columns=30_000,
                            rows=30_000,
                            column_step=(10, 0),
                            row_step=(0, 10),
                        ),
                    ),
                ),
            ),
        ),
    )
    original = Instance.placement
    visited: list[tuple[int, int]] = []

    def record(instance: Instance, column: int, row: int) -> tuple[str, Transform]:
        visited.append((column, row))
        assert len(visited) == 1
        return original(instance, column, row)

    monkeypatch.setattr(Instance, "placement", record)
    selected = extract_window(layout, BBox((123451, 234561), (123453, 234563))).unwrap()
    assert visited == [(12345, 23456)]
    assert selected.polygons == {
        "WG": (
            to_polygon(rect)
            .unwrap()
            .transformed(Transform(translation=(123450, 234560))),
        )
    }
    assert [port.path for port in selected.ports] == ["a/23456/12345/p"]


def test_window_reports_missing_cell_and_inverted_bounds() -> None:
    result = extract_window(nested_layout(), BBox((1, 0), (0, 1)), "missing")
    assert result.value is None
    assert [item.code for item in result.diagnostics] == [
        "layout.cell.unknown",
        "layout.window.invalid",
    ]
