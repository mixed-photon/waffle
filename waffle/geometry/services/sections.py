"""Point membership in simple outlines and inward offsets of convex outlines."""

from __future__ import annotations

from fractions import Fraction
from math import isfinite, nextafter

from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.geometry.transform import Point


def _cross(a: Point, b: Point, point: Point) -> int:
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def is_convex(polygon: Polygon) -> bool:
    """Test the turns of a simple, counter-clockwise polygon in linear time."""
    points = polygon.points
    return all(_cross(points[i - 2], points[i - 1], point) >= 0 for i, point in enumerate(points))


def _contains(points: tuple[Point, ...], point: Point) -> bool:
    x, y = point
    inside = False
    for i, b in enumerate(points):
        a = points[i - 1]
        cross = _cross(a, b, point)
        if (
            cross == 0
            and min(a[0], b[0]) <= x <= max(a[0], b[0])
            and min(a[1], b[1]) <= y <= max(a[1], b[1])
        ):
            return True
        # Count crossings to the right. The half-open y interval counts shared vertices once.
        if (a[1] > y) != (b[1] > y) and (cross > 0) == (b[1] > a[1]):
            inside = not inside
    return inside


def contains_at_inset(polygon: Polygon, point: Point, inset_dbu: float) -> bool:
    """Test closed membership in a simple outline inset from its edges by a distance in dbu.

    A nonzero inset requires a convex outline. The input ring is assumed to be simple.
    """
    if not isfinite(inset_dbu) or inset_dbu < 0:
        raise ValueError("inset must be finite and nonnegative")
    points = polygon.points
    if inset_dbu == 0:
        return _contains(points, point)
    if not is_convex(polygon):
        raise ValueError("a positive inset requires a convex polygon")

    inset = Fraction(inset_dbu)
    axis_limit = inset**2
    # Allow half the spacing to the preceding float on diagonal edges. Comparing squares
    # with integer products keeps the result independent of how edge lengths round.
    diagonal_limit = ((inset + Fraction(nextafter(inset_dbu, 0.0))) / 2) ** 2
    for i, b in enumerate(points):
        a = points[i - 1]
        cross = _cross(a, b, point)
        if cross <= 0:
            return False
        dx, dy = b[0] - a[0], b[1] - a[1]
        limit = diagonal_limit if dx and dy else axis_limit
        if cross * cross * limit.denominator < (dx * dx + dy * dy) * limit.numerator:
            return False
    return True
