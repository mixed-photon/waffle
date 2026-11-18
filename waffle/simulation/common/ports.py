"""Port placement against the geometry on the port's layer.

A port is valid when the waveguide drawn on its layer ends flush at the port plane:

* The reference plane is the line through `center` perpendicular to `orientation`.
* Take every polygon edge on that line whose outward normal (polygons are counter-clockwise,
  so the interior is on the left of an edge) equals `orientation`, merge touching ones, and
  remove the stretches covered by edges with the opposite normal (another polygon abutting
  there makes the plane interior). The piece containing `center` is the waveguide
  cross-section at the port.
* It must equal the port segment `center +- width/2` exactly: otherwise
  `physics.port.width_mismatch`.
* No such piece but opposite-normal edges cover `center` (net of the facing ones): the port
  points into the geometry, `physics.port.orientation`.
* No edge covers `center` at all: `physics.port.not_on_geometry`.

Only edges exactly on the port line are compared; polygons that overlap rather than abut
(an edge buried inside another polygon) are not merged, so such an edge still counts as a
boundary. Only the port's own layer is considered (a rib's slab
wings on another layer do not change the cross-section a port is compared with).
"""

from __future__ import annotations

from collections.abc import Sequence

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.layout.layout import Port


def check_port_geometry(
    path: str, port: Port, polygons: Sequence[Polygon], *, subject: str
) -> list[Diagnostic]:
    """Diagnostics for a port that does not sit on the end of the waveguide on its layer."""
    a, b = (0, 1) if port.orientation in (0, 180) else (1, 0)  # plane axis, in-plane axis
    sign = 1 if port.orientation in (0, 90) else -1
    plane, along = port.center[a], port.center[b]
    facing: list[tuple[int, int]] = []
    opposing: list[tuple[int, int]] = []
    for polygon in polygons:
        ring = polygon.points
        for p, q in zip(ring, (*ring[1:], ring[0]), strict=True):
            if p[a] != plane or q[a] != plane:
                continue
            step = q[b] - p[b]
            outward = sign * (step if a == 0 else -step)
            (facing if outward > 0 else opposing).append((min(p[b], q[b]), max(p[b], q[b])))

    def diagnostic(code: str, message: str) -> list[Diagnostic]:
        return [
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                message=f"port {path!r}: {message}",
                subject=subject,
                location=("ports", path),
            )
        ]

    cross_section = _piece_containing(_subtract(facing, opposing), along)
    if cross_section is not None:
        low, high = cross_section
        if 2 * low == 2 * along - port.width and 2 * high == 2 * along + port.width:
            return []
        return diagnostic(
            "physics.port.width_mismatch",
            f"port width {port.width} dbu differs from the {high - low} dbu "
            f"waveguide cross-section on layer {port.layer!r}",
        )
    if _piece_containing(_subtract(opposing, facing), along) is not None:
        return diagnostic(
            "physics.port.orientation",
            f"orientation {port.orientation} points into the geometry on layer {port.layer!r}",
        )
    return diagnostic(
        "physics.port.not_on_geometry",
        f"center {port.center} is not on the boundary of any polygon on layer {port.layer!r}",
    )


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The union of `intervals`; touching ones merge."""
    merged: list[tuple[int, int]] = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _subtract(
    intervals: list[tuple[int, int]], cut: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The parts of the union of `intervals` not covered by the union of `cut`."""
    pieces = _merge(intervals)
    for cut_low, cut_high in _merge(cut):
        remaining: list[tuple[int, int]] = []
        for low, high in pieces:
            if cut_high <= low or cut_low >= high:
                remaining.append((low, high))
                continue
            if low < cut_low:
                remaining.append((low, cut_low))
            if cut_high < high:
                remaining.append((cut_high, high))
        pieces = remaining
    return pieces


def _piece_containing(pieces: list[tuple[int, int]], value: int) -> tuple[int, int] | None:
    return next(((low, high) for low, high in pieces if low <= value <= high), None)
