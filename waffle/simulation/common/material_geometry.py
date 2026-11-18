"""Inspect the material of an extruded 3D region at explicit database-grid samples."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.shapes import BBox
from waffle.contracts.geometry.transform import Point
from waffle.contracts.physics.problem import Extrusion3D, Prism
from waffle.geometry.services.sections import contains_at_inset, is_convex
from waffle.geometry.services.shapes import is_simple

type SectionAxis = Literal["x", "y"]


@dataclass(frozen=True)
class MaterialSection:
    """One vertical plane sampled at the supplied positions and heights.

    The axis names the fixed coordinate, in dbu. Positions are in dbu along
    the other in-plane axis. Rows correspond to heights in picometres and
    entries in each row correspond to positions, both in the supplied order.
    """

    axis: SectionAxis
    coordinate: int
    positions: tuple[int, ...]
    heights_pm: tuple[int, ...]
    materials: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _PrismGeometry:
    prism: Prism
    bounds: BBox
    cotangent: float


@dataclass(frozen=True)
class _Sampler:
    description: Extrusion3D
    dbu_pm: int
    prisms: tuple[_PrismGeometry, ...]

    def sample(self, point: Point, z_pm: int) -> str:
        # Last matching prism wins over every film. The last matching film
        # wins over the background, following the extrusion's paint order.
        for entry in reversed(self.prisms):
            prism = entry.prism
            if prism.z_min_pm <= z_pm < prism.z_max_pm and entry.bounds.contains(point):
                inset = 0.0
                if entry.cotangent:
                    inset = (z_pm - prism.z_min_pm) / self.dbu_pm * entry.cotangent
                if math.isfinite(inset) and contains_at_inset(prism.polygon, point, inset):
                    return prism.material
        for film in reversed(self.description.films):
            if film.z_min_pm <= z_pm < film.z_max_pm:
                return film.material
        return self.description.background


def _failure[T](code: str, message: str, *location: str | int) -> Result[T]:
    return Result(
        None,
        (
            Diagnostic(
                severity=Severity.ERROR, code=code, message=message, location=location
            ),
        ),
    )


def _prepare_sampler(description: Extrusion3D, dbu_pm: int) -> Result[_Sampler]:
    if type(dbu_pm) is not int or dbu_pm <= 0:
        return _failure(
            "physics.geometry.invalid_dbu",
            "dbu_pm must be a positive integer",
            "dbu_pm",
        )
    for index, film in enumerate(description.films):
        if film.z_min_pm >= film.z_max_pm:
            return _failure(
                "physics.geometry.invalid_extent",
                "film needs z_min_pm < z_max_pm",
                "films",
                index,
            )
    entries: list[_PrismGeometry] = []
    for index, prism in enumerate(description.prisms):
        if prism.z_min_pm >= prism.z_max_pm:
            return _failure(
                "physics.geometry.invalid_extent",
                "prism needs z_min_pm < z_max_pm",
                "prisms",
                index,
            )
        if not is_simple(prism.polygon):
            return _failure(
                "physics.geometry.invalid_footprint",
                "prism footprint must be a simple polygon",
                "prisms",
                index,
                "polygon",
            )
        angle = prism.sidewall_angle_deg
        cotangent = 0.0
        if angle is not None:
            if not is_convex(prism.polygon):
                return _failure(
                    "physics.geometry.unsupported_taper",
                    "a tapered prism needs a convex footprint for material inspection",
                    "prisms",
                    index,
                    "polygon",
                )
            tangent = 1.0 if angle == 45.0 else math.tan(math.radians(angle))
            if tangent == 0 or not math.isfinite(1.0 / tangent):
                return _failure(
                    "physics.geometry.invalid_sidewall_angle",
                    "sidewall inset cannot be represented for this angle",
                    "prisms",
                    index,
                    "sidewall_angle_deg",
                )
            cotangent = 1.0 / tangent
        bounds = BBox.of(prism.polygon.points)
        assert bounds is not None
        entries.append(_PrismGeometry(prism, bounds, cotangent))
    return Result.of(_Sampler(description, dbu_pm, tuple(entries)))


def _outside_domain(
    description: Extrusion3D, points: tuple[Point, ...], heights_pm: tuple[int, ...]
) -> bool:
    domain = description.domain
    window = BBox(domain.lo, domain.hi)
    return any(not window.contains(point) for point in points) or any(
        not domain.z_min_pm <= height < domain.z_max_pm for height in heights_pm
    )


def material_at(
    description: Extrusion3D, dbu_pm: int, point: Point, z_pm: int
) -> Result[str]:
    """Find the material at a point within the extrusion's domain.

    XY bounds and footprints include their edges. Z ranges include their
    lower endpoints and exclude their upper endpoints.
    """
    if _outside_domain(description, (point,), (z_pm,)):
        return _failure(
            "physics.geometry.outside_domain", "sample is outside the region", "point"
        )
    prepared = _prepare_sampler(description, dbu_pm)
    if prepared.value is None:
        return Result(None, prepared.diagnostics)
    return Result.of(prepared.value.sample(point, z_pm))


def material_section(
    description: Extrusion3D,
    dbu_pm: int,
    *,
    axis: SectionAxis,
    coordinate: int,
    positions: tuple[int, ...],
    heights_pm: tuple[int, ...],
) -> Result[MaterialSection]:
    """Sample a vertical section of the same volume evaluated by material_at."""
    if not positions or not heights_pm:
        return _failure(
            "physics.geometry.empty_section",
            "a section needs at least one position and height",
            "section",
        )
    match axis:
        case "x":
            points = tuple((coordinate, position) for position in positions)
        case "y":
            points = tuple((position, coordinate) for position in positions)
        case _:
            raise ValueError(f"section axis must be 'x' or 'y', got {axis!r}")
    if _outside_domain(description, points, heights_pm):
        return _failure(
            "physics.geometry.outside_domain",
            "section contains samples outside the region",
            "section",
        )
    prepared = _prepare_sampler(description, dbu_pm)
    if prepared.value is None:
        return Result(None, prepared.diagnostics)
    sampler = prepared.value
    return Result.of(
        MaterialSection(
            axis=axis,
            coordinate=coordinate,
            positions=positions,
            heights_pm=heights_pm,
            materials=tuple(
                tuple(sampler.sample(point, height) for point in points)
                for height in heights_pm
            ),
        )
    )
