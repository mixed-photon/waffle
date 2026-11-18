from __future__ import annotations

from waffle.contracts.geometry.shapes import BBox, Path, Polygon, Rectangle
from waffle.geometry.services.shapes import (
    is_simple,
    path_to_polygon,
    polygon_intersects_box,
    to_polygon,
    validate_shape,
)


def codes(diagnostics: list) -> list[str]:  # type: ignore[type-arg]
    return [d.code for d in diagnostics]


def test_straight_path_outline() -> None:
    flush = path_to_polygon(Path(points=((0, 0), (10, 0)), width=4)).unwrap()
    assert flush.points == ((0, -2), (10, -2), (10, 2), (0, 2))
    square = path_to_polygon(Path(points=((0, 0), (10, 0)), width=4, end="square")).unwrap()
    assert square.points == ((-2, -2), (12, -2), (12, 2), (-2, 2))
    vertical = path_to_polygon(Path(points=((0, 0), (0, -10)), width=2)).unwrap()
    assert vertical.points == ((-1, -10), (1, -10), (1, 0), (-1, 0))


def test_l_shaped_path_is_mitred() -> None:
    poly = path_to_polygon(Path(points=((0, 0), (10, 0), (10, 10)), width=2)).unwrap()
    assert poly.points == ((0, -1), (11, -1), (11, 10), (9, 10), (9, 1), (0, 1))
    assert is_simple(poly)


def test_collinear_path_points_do_not_change_the_outline() -> None:
    a = path_to_polygon(Path(points=((0, 0), (5, 0), (10, 0)), width=4)).unwrap()
    b = path_to_polygon(Path(points=((0, 0), (10, 0)), width=4)).unwrap()
    assert a == b


def test_invalid_paths_fail_explicitly() -> None:
    cases = {
        "geometry.path.odd_width": Path(points=((0, 0), (10, 0)), width=3),
        "geometry.path.zero_length": Path(points=((0, 0), (0, 0)), width=2),
        "geometry.unsupported.path_segment": Path(points=((0, 0), (10, 5)), width=2),
        "geometry.path.reversal": Path(points=((0, 0), (10, 0), (4, 0)), width=2),
    }
    for code, path in cases.items():
        result = path_to_polygon(path, subject="layout:x", location=("p",))
        assert result.value is None
        assert code in codes(list(result.diagnostics))


def test_to_polygon_expands_rectangles() -> None:
    rect = Rectangle(lo=(0, 0), hi=(4, 2))
    assert to_polygon(rect).unwrap() == Polygon(points=((0, 0), (4, 0), (4, 2), (0, 2)))


def test_polygon_intersection_uses_the_outline_and_includes_boundary_contact() -> None:
    triangle = Polygon(points=((0, 0), (10, 0), (0, 10)))
    assert polygon_intersects_box(triangle, BBox((2, 2), (3, 3)))
    assert polygon_intersects_box(triangle, BBox((0, 4), (10, 5)))
    assert polygon_intersects_box(triangle, BBox((4, 6), (4, 6)))
    assert not polygon_intersects_box(triangle, BBox((6, 6), (8, 8)))
    assert not polygon_intersects_box(triangle, BBox((5, 6), (5, 6)))

    concave = Polygon(points=((0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)))
    assert not polygon_intersects_box(concave, BBox((5, 5), (6, 6)))
    assert polygon_intersects_box(concave, BBox((2, 5), (6, 6)))


def test_self_intersection_is_detected() -> None:
    bowtie = Polygon(points=((0, 0), (4, 4), (4, 0), (0, 3)))
    assert not is_simple(bowtie)
    assert codes(validate_shape(bowtie)) == ["geometry.polygon.self_intersecting"]
    assert validate_shape(bowtie, check_simple=False) == []
    pinched = Polygon(points=((0, 0), (4, 0), (4, 4), (2, 0), (2, 3), (0, 3)))
    assert not is_simple(pinched)
    assert is_simple(Polygon(points=((0, 0), (4, 0), (4, 4), (0, 4))))


def test_self_overlapping_path_is_not_simple() -> None:
    loop = Path(points=((0, 0), (10, 0), (10, 10), (2, 10), (2, -5)), width=4)
    assert "geometry.polygon.self_intersecting" in codes(validate_shape(loop))
