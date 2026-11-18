from __future__ import annotations

import pytest

from waffle.contracts.geometry.shapes import BBox, Path, Polygon, Rectangle, shape_key
from waffle.contracts.geometry.transform import Transform


def test_equal_polygons_compare_equal_whatever_the_input_ring() -> None:
    ccw = Polygon(points=((0, 0), (4, 0), (4, 2), (0, 2)))
    assert Polygon(points=((4, 2), (0, 2), (0, 0), (4, 0))) == ccw  # rotated start
    assert Polygon(points=((0, 0), (0, 2), (4, 2), (4, 0))) == ccw  # clockwise
    assert Polygon(points=((0, 0), (2, 0), (4, 0), (4, 2), (0, 2), (0, 2))) == ccw
    assert ccw.points == ((0, 0), (4, 0), (4, 2), (0, 2))


def test_degenerate_polygons_are_rejected() -> None:
    for points in (((0, 0), (1, 1)), ((0, 0), (1, 1), (2, 2)), ((0, 0), (5, 0), (0, 0))):
        with pytest.raises(ValueError):
            Polygon(points=points)


def test_non_integer_coordinates_are_rejected() -> None:
    with pytest.raises(ValueError):
        Polygon(points=((0, 0), (1.5, 0), (0, 2)))  # type: ignore[arg-type]


def test_rectangle_requires_ordered_corners_and_spanning_sorts() -> None:
    with pytest.raises(ValueError):
        Rectangle(lo=(5, 0), hi=(0, 3))
    with pytest.raises(ValueError):
        Rectangle(lo=(0, 0), hi=(0, 3))
    assert Rectangle.spanning((5, 3), (0, 0)) == Rectangle(lo=(0, 0), hi=(5, 3))


def test_shapes_transform_and_renormalize() -> None:
    mirror = Transform(mirror=True, translation=(10, 0))
    triangle = Polygon(points=((0, 0), (4, 0), (0, 2)))
    assert triangle.transformed(mirror).points == ((10, -2), (14, 0), (10, 0))
    rect = Rectangle(lo=(0, 0), hi=(20, 5))
    assert rect.transformed(Transform(rotation=90, translation=(100, 0))) == Rectangle(
        lo=(95, 0), hi=(100, 20)
    )
    path = Path(points=((0, 0), (10, 0)), width=4)
    assert path.transformed(Transform(rotation=90)).points == ((0, 0), (0, 10))


def test_shape_key_orders_across_types_deterministically() -> None:
    shapes = [
        Path(points=((0, 0), (1, 0)), width=2),
        Rectangle(lo=(0, 0), hi=(1, 1)),
        Polygon(points=((0, 0), (1, 0), (0, 1))),
    ]
    assert [s.type for s in sorted(shapes, key=shape_key)] == ["path", "polygon", "rectangle"]


def test_bbox() -> None:
    assert BBox.of([(1, 5), (-2, 3)]) == BBox((-2, 3), (1, 5))
    assert BBox.of([]) is None
