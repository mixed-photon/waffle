"""Shape services: path outlines, polygon conversion, and shape validation."""

from __future__ import annotations

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.shapes import BBox, Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Point
from waffle.contracts.geometry.unsupported import Unsupported, unsupported
from waffle.geometry.services.sections import contains_at_inset

Location = tuple[str | int, ...]


def _error(code: str, message: str, subject: str | None, location: Location) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject, location=location
    )


def path_to_polygon(
    path: Path, *, subject: str | None = None, location: Location = ()
) -> Result[Polygon]:
    """The exact outline of a Manhattan path, with mitred corners.

    Errors: `geometry.unsupported.path_segment` (diagonal segment), `geometry.path.odd_width`
    (half the width must be an integer), `geometry.path.zero_length` (repeated point), and
    `geometry.path.reversal` (a segment doubling back on the previous one).
    """
    found: list[Diagnostic] = []
    if path.width % 2:
        found.append(
            _error("geometry.path.odd_width", f"width {path.width} is odd", subject, location)
        )
    directions: list[Point] = []
    for i, (a, b) in enumerate(zip(path.points, path.points[1:], strict=False)):
        at = (*location, "points", i)
        if a == b:
            found.append(_error("geometry.path.zero_length", f"segment {i} is empty", subject, at))
        elif a[0] != b[0] and a[1] != b[1]:
            found.append(
                unsupported(
                    Unsupported.PATH_SEGMENT,
                    f"segment {i} is diagonal",
                    subject=subject,
                    location=at,
                )
            )
        else:
            directions.append(((b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])))
    if found:
        return Result(None, tuple(found))
    for i, (d, e) in enumerate(zip(directions, directions[1:], strict=False)):
        if (d[0] + e[0], d[1] + e[1]) == (0, 0):
            found.append(
                _error(
                    "geometry.path.reversal",
                    f"segment {i + 1} doubles back",
                    subject,
                    (*location, "points", i + 1),
                )
            )
    if found:
        return Result(None, tuple(found))
    half = path.width // 2
    square = path.end == "square"
    left: list[Point] = []
    right: list[Point] = []
    for i, (x, y) in enumerate(path.points):
        incoming = directions[i - 1] if i > 0 else None
        outgoing = directions[i] if i < len(directions) else None
        # left normal of a direction (dx, dy) is (-dy, dx); a mitred vertex offsets by the
        # sum of its distinct adjacent normals.
        normals = {(-d[1], d[0]) for d in (incoming, outgoing) if d is not None}
        nx, ny = sum(n[0] for n in normals), sum(n[1] for n in normals)
        ex = ey = 0
        if square and incoming is None and outgoing is not None:
            ex, ey = -outgoing[0], -outgoing[1]
        elif square and outgoing is None and incoming is not None:
            ex, ey = incoming
        left.append((x + half * (nx + ex), y + half * (ny + ey)))
        right.append((x + half * (-nx + ex), y + half * (-ny + ey)))
    return Result.of(Polygon(points=(*left, *reversed(right))))


def to_polygon(
    shape: Polygon | Rectangle | Path, *, subject: str | None = None, location: Location = ()
) -> Result[Polygon]:
    """Any shape as a polygon (rectangles and paths are expanded)."""
    match shape:
        case Polygon():
            return Result.of(shape)
        case Rectangle():
            (x0, y0), (x1, y1) = shape.lo, shape.hi
            return Result.of(Polygon(points=((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
        case Path():
            return path_to_polygon(shape, subject=subject, location=location)


def _side(a: Point, b: Point, c: Point) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (cross > 0) - (cross < 0)


def _within(a: Point, b: Point, c: Point) -> bool:
    """Whether `c`, known to be collinear with `a`-`b`, lies on the segment."""
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def _segments_meet(a: Point, b: Point, c: Point, d: Point) -> bool:
    s1, s2, s3, s4 = _side(a, b, c), _side(a, b, d), _side(c, d, a), _side(c, d, b)
    if s1 != s2 and s3 != s4:
        return True
    return (
        (s1 == 0 and _within(a, b, c))
        or (s2 == 0 and _within(a, b, d))
        or (s3 == 0 and _within(c, d, a))
        or (s4 == 0 and _within(c, d, b))
    )


def polygon_intersects_box(polygon: Polygon, box: BBox) -> bool:
    """Test a simple polygon against a closed box in integer database units."""
    points = polygon.points
    if any(box.contains(point) for point in points):
        return True

    edges = tuple(zip(points, (*points[1:], points[0]), strict=True))
    (x0, y0), (x1, y1) = box.lo, box.hi
    corners = (box.lo, (x1, y0), box.hi, (x0, y1))
    box_edges = tuple(zip(corners, (*corners[1:], corners[0]), strict=True))
    if any(_segments_meet(a, b, c, d) for a, b in edges for c, d in box_edges):
        return True

    # With no boundary intersections, the box is either entirely inside or outside.
    return contains_at_inset(polygon, box.lo, 0.0)


def is_simple(polygon: Polygon) -> bool:
    """True if no two non-adjacent edges touch or cross. Quadratic in the vertex count."""
    pts = polygon.points
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent through the closing edge
            if _segments_meet(a, b, pts[j], pts[(j + 1) % n]):
                return False
    return True


def validate_shape(
    shape: Polygon | Rectangle | Path,
    *,
    check_simple: bool = True,
    subject: str | None = None,
    location: Location = (),
) -> list[Diagnostic]:
    """Everything wrong with `shape` beyond what its model already enforces."""
    result = to_polygon(shape, subject=subject, location=location)
    if result.value is None:
        return list(result.diagnostics)
    if check_simple and not isinstance(shape, Rectangle) and not is_simple(result.value):
        return [
            _error(
                "geometry.polygon.self_intersecting",
                "outline touches or crosses itself",
                subject,
                location,
            )
        ]
    return []
