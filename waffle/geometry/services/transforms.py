"""Building `Transform`s from upstream data: angles in degrees, magnification, `Placement`.

Anything the IR cannot represent exactly (non-Manhattan angles, magnification) is reported
with the shared `geometry.unsupported.*` codes rather than rounded.
"""

from __future__ import annotations

from waffle.contracts.component.design import Placement
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.transform import Orientation, Point, Transform
from waffle.contracts.geometry.unsupported import Unsupported, unsupported
from waffle.geometry.services.grid import Rounding, to_dbu

Location = tuple[str | int, ...]
_ANGLES: dict[int, Orientation] = {0: 0, 90: 90, 180: 180, 270: 270}


def manhattan_angle(
    degrees: float, *, subject: str | None = None, location: Location = ()
) -> Result[Orientation]:
    """`degrees` (any multiple of 90, any sign) as 0, 90, 180, or 270; otherwise unsupported."""
    if degrees % 90 != 0:
        return Result(
            None,
            (
                unsupported(
                    Unsupported.ROTATION,
                    f"{degrees} degrees",
                    subject=subject,
                    location=location,
                ),
            ),
        )
    return Result.of(_ANGLES[int(degrees) % 360])


def transform_from_parts(
    translation: Point,
    rotation_deg: float,
    mirror: bool,
    magnification: float = 1.0,
    *,
    subject: str | None = None,
    location: Location = (),
) -> Result[Transform]:
    """The transform for GDS-style reference data (translation in dbu, rotation in degrees,
    mirror about the x axis applied first, magnification which must be exactly 1)."""
    found: list[Diagnostic] = []
    angle = manhattan_angle(rotation_deg, subject=subject, location=(*location, "rotation"))
    found.extend(angle.diagnostics)
    if magnification != 1.0:
        found.append(
            unsupported(
                Unsupported.MAGNIFICATION,
                f"magnification {magnification}",
                subject=subject,
                location=(*location, "magnification"),
            )
        )
    if angle.value is None or found:
        return Result(None, tuple(found))
    return Result.of(Transform(mirror=mirror, rotation=angle.value, translation=translation))


def placement_to_transform(
    placement: Placement,
    grid: Grid,
    rounding: Rounding = Rounding.EXACT,
    *,
    subject: str | None = None,
) -> Result[Transform]:
    """The layout transform of a circuit placement on `grid`.

    A placement's `mirror` is a mirror about the x axis applied before `rotation_deg`
    (counter-clockwise), then the origin translation, exactly as `Transform` defines. The
    origin must be on the grid unless `rounding` allows snapping (reported as a warning).
    """
    found: list[Diagnostic] = []
    coords: list[int] = []
    for axis, quantity in enumerate(placement.origin):
        snapped = to_dbu(quantity, grid, rounding, subject=subject, location=("origin", axis))
        found.extend(snapped.diagnostics)
        coords.append(snapped.value.dbu if snapped.value else 0)
    transform = transform_from_parts(
        (coords[0], coords[1]),
        placement.rotation_deg,
        placement.mirror,
        subject=subject,
    )
    found.extend(transform.diagnostics)
    return Result.of(transform.value, found) if transform.value else Result(None, tuple(found))
