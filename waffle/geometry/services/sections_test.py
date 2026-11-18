from __future__ import annotations

from math import inf, nextafter, sqrt

import pytest

from waffle.contracts.geometry.shapes import Polygon
from waffle.geometry.services.sections import contains_at_inset, is_convex


def test_zero_inset_uses_the_concave_outline_and_includes_its_edges() -> None:
    polygon = Polygon(points=((0, 0), (8, 0), (8, 3), (3, 3), (3, 8), (0, 8)))
    assert not is_convex(polygon)
    assert contains_at_inset(polygon, (5, 2), 0)
    assert contains_at_inset(polygon, (2, 5), 0)
    assert contains_at_inset(polygon, (3, 5), 0)
    assert contains_at_inset(polygon, (5, 3), 0)
    assert contains_at_inset(polygon, (3, 3), 0)
    assert not contains_at_inset(polygon, (5, 5), 0)
    assert not contains_at_inset(polygon, (3, 9), 0)
    with pytest.raises(ValueError, match="convex polygon"):
        contains_at_inset(polygon, (2, 2), 1)


def test_rectangular_inset_is_closed_and_can_collapse() -> None:
    polygon = Polygon(points=((0, 0), (10, 0), (10, 4), (0, 4)))
    assert is_convex(polygon)
    assert contains_at_inset(polygon, (2, 2), 2)
    assert contains_at_inset(polygon, (8, 2), 2)
    assert not contains_at_inset(polygon, (1, 2), 2)
    assert not contains_at_inset(polygon, (5, 1), 2)
    assert not contains_at_inset(polygon, (5, 2), nextafter(2.0, inf))


def test_diagonal_inset_includes_the_rounded_boundary() -> None:
    diamond = Polygon(points=((0, 0), (8, 8), (0, 16), (-8, 8)))
    assert is_convex(diamond)
    assert contains_at_inset(diamond, (3, 3), 0)
    assert contains_at_inset(diamond, (0, 2), sqrt(2))
    assert contains_at_inset(diamond, (0, 3), sqrt(2))
    assert not contains_at_inset(diamond, (0, 1), sqrt(2))
    assert not contains_at_inset(diamond, (0, 2), nextafter(sqrt(2), inf))


@pytest.mark.parametrize("inset_dbu", [-1.0, -inf, inf, float("nan")])
def test_invalid_inset_raises(inset_dbu: float) -> None:
    polygon = Polygon(points=((0, 0), (2, 0), (0, 2)))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        contains_at_inset(polygon, (1, 1), inset_dbu)
