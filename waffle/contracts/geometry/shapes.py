"""The initial shape subset: simple polygons, rectangles, and Manhattan paths.

Holes and curves are not representable (see `unsupported`). Polygons are normalized on
construction so equal polygons compare equal: collinear and duplicate vertices are removed,
the ring is counter-clockwise, and it starts at its lexicographically smallest vertex.
Rectangles require `lo < hi` on both axes (`Rectangle.spanning` sorts corners). A path is a
centre line with a width; its polygon form is computed by
`waffle.geometry.services.shapes.path_to_polygon`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from waffle.contracts.geometry.transform import Point, Transform


def _cross(o: Point, a: Point, b: Point) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _drop_redundant(points: Iterable[Point]) -> list[Point]:
    """Remove duplicate and collinear vertices of a closed ring in one pass."""
    ring: list[Point] = []
    for p in points:
        while True:
            if ring and ring[-1] == p:
                break
            if len(ring) >= 2 and _cross(ring[-2], ring[-1], p) == 0:
                ring.pop()
                continue
            ring.append(p)
            break
    while len(ring) >= 3:
        if ring[-1] == ring[0] or _cross(ring[-2], ring[-1], ring[0]) == 0:
            ring.pop()
        elif _cross(ring[-1], ring[0], ring[1]) == 0:
            ring.pop(0)
        else:
            break
    return ring


def _normalize_ring(points: tuple[Point, ...]) -> tuple[Point, ...]:
    ring = _drop_redundant(points)
    if len(ring) < 3:
        raise ValueError("polygon needs at least 3 non-collinear vertices")
    twice_area = sum(
        ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
        for i in range(len(ring))
    )
    if twice_area == 0:
        raise ValueError("polygon has zero area")
    if twice_area < 0:
        ring.reverse()
    start = ring.index(min(ring))
    return tuple(ring[start:] + ring[:start])


class Polygon(BaseModel):
    """A polygon without holes. `points` is the normalized ring (closing vertex omitted).

    Self-intersection is not checked here (it is quadratic); `validate_layout` does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["polygon"] = "polygon"
    points: tuple[Point, ...]

    @field_validator("points")
    @classmethod
    def _normalize(cls, points: tuple[Point, ...]) -> tuple[Point, ...]:
        return _normalize_ring(points)

    def transformed(self, transform: Transform) -> Polygon:
        return Polygon(points=tuple(transform.apply(p) for p in self.points))


class Rectangle(BaseModel):
    """An axis-aligned box from `lo` (inclusive) to `hi`, with `lo < hi` on both axes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["rectangle"] = "rectangle"
    lo: Point
    hi: Point

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lo[0] >= self.hi[0] or self.lo[1] >= self.hi[1]:
            raise ValueError("rectangle needs lo < hi on both axes")
        return self

    @classmethod
    def spanning(cls, a: Point, b: Point) -> Rectangle:
        """The rectangle with opposite corners `a` and `b`, in either order."""
        return cls(lo=(min(a[0], b[0]), min(a[1], b[1])), hi=(max(a[0], b[0]), max(a[1], b[1])))

    def transformed(self, transform: Transform) -> Rectangle:
        return Rectangle.spanning(transform.apply(self.lo), transform.apply(self.hi))


class Path(BaseModel):
    """A centre line of at least two points and a full `width`.

    `flush` ends at the first and last point; `square` extends both ends by half the width.
    Supported paths have horizontal or vertical segments, an even width, and no reversal;
    `path_to_polygon` reports violations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["path"] = "path"
    points: tuple[Point, ...] = Field(min_length=2)
    width: StrictInt = Field(gt=0)
    end: Literal["flush", "square"] = "flush"

    def transformed(self, transform: Transform) -> Path:
        return self.model_copy(update={"points": tuple(transform.apply(p) for p in self.points)})


Shape = Annotated[Polygon | Rectangle | Path, Field(discriminator="type")]


def shape_key(shape: Polygon | Rectangle | Path) -> tuple[object, ...]:
    """A total order over shapes, used to store shapes in a deterministic order."""
    match shape:
        case Polygon():
            return ("polygon", shape.points)
        case Rectangle():
            return ("rectangle", shape.lo, shape.hi)
        case Path():
            return ("path", shape.end, shape.width, shape.points)


@dataclass(frozen=True)
class BBox:
    """An inclusive axis-aligned bounding box in database units."""

    lo: Point
    hi: Point

    def contains(self, point: Point) -> bool:
        return all(
            lo <= coordinate <= hi
            for lo, coordinate, hi in zip(self.lo, point, self.hi, strict=True)
        )

    def intersects(self, other: BBox) -> bool:
        return all(
            lo <= other_hi and other_lo <= hi
            for lo, hi, other_lo, other_hi in zip(
                self.lo, self.hi, other.lo, other.hi, strict=True
            )
        )

    def transformed(self, transform: Transform) -> BBox:
        """Return the bounds under an exact orthogonal transform."""
        ax, ay = transform.apply(self.lo)
        bx, by = transform.apply(self.hi)
        return BBox((min(ax, bx), min(ay, by)), (max(ax, bx), max(ay, by)))

    @classmethod
    def of(cls, points: Iterable[Point]) -> BBox | None:
        pts = list(points)
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return cls((min(xs), min(ys)), (max(xs), max(ys)))
