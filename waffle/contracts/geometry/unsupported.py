"""One place for the diagnostics of constructs the geometry and layout IR does not support.

Adapters that meet such a construct in upstream data call `unsupported` so every backend
reports it with the same stable code. The IR itself cannot express these constructs.
"""

from __future__ import annotations

from enum import StrEnum

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity


class Unsupported(StrEnum):
    CURVE = "geometry.unsupported.curve"
    POLYGON_HOLES = "geometry.unsupported.polygon_holes"
    SHAPE = "geometry.unsupported.shape"
    PATH_END = "geometry.unsupported.path_end"
    PATH_SEGMENT = "geometry.unsupported.path_segment"
    ROTATION = "geometry.unsupported.rotation"
    MAGNIFICATION = "geometry.unsupported.magnification"
    ARRAY_INSTANCE = "layout.unsupported.array_instance"
    EXTERNAL_REFERENCE = "layout.unsupported.external_reference"


_SUMMARY: dict[Unsupported, str] = {
    Unsupported.CURVE: "curves (arcs, splines) must be discretized to polygons upstream",
    Unsupported.POLYGON_HOLES: "polygons with holes are not supported",
    Unsupported.SHAPE: "shape type is not supported (polygon, rectangle, path only)",
    Unsupported.PATH_END: "path end style is not supported (flush, square only)",
    Unsupported.PATH_SEGMENT: "path segments must be horizontal or vertical",
    Unsupported.ROTATION: "only rotations by multiples of 90 degrees are supported",
    Unsupported.MAGNIFICATION: "magnification other than 1 is not supported",
    Unsupported.ARRAY_INSTANCE: "array instance is outside the supported repetition subset",
    Unsupported.EXTERNAL_REFERENCE: "instances must reference a cell in the same layout",
}


def unsupported(
    construct: Unsupported,
    detail: str,
    *,
    subject: str | None = None,
    location: tuple[str | int, ...] = (),
) -> Diagnostic:
    """The error diagnostic for `construct`, with `detail` saying where it was met."""
    return Diagnostic(
        severity=Severity.ERROR,
        code=construct.value,
        message=f"{_SUMMARY[construct]}: {detail}",
        subject=subject,
        location=location,
    )
