"""Vertical material columns through a technology's process stack.

This is the exact input a layout-to-physics step needs to build either a 3D extrusion or a
declared effective-index reduction; it computes neither. Heights are SI metres. Callers
validate the technology first (levels ascending and non-overlapping).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from waffle.contracts.technology.revision import Patterned, TechnologyRevision
from waffle.technology.validate import Z_TOLERANCE_M


@dataclass(frozen=True)
class Segment:
    """A z interval `[z_min_m, z_max_m]` of one material."""

    z_min_m: float
    z_max_m: float
    material: str


def vertical_column(tech: TechnologyRevision, present: Collection[str]) -> tuple[Segment, ...]:
    """The materials from bottom to top at a lateral position where exactly the layers
    in `present` are drawn.

    A patterned level contributes its `material` when any of its layers is present and its
    `fill` otherwise; a blanket level always contributes its `material`. z intervals inside
    the stack not covered by any level hold the background, and adjacent segments of one
    material are merged. Above and below the returned segments lies the background.
    ValueError for a layer name that is not in the layer table.
    """
    unknown = sorted(name for name in present if tech.layer(name) is None)
    if unknown:
        raise ValueError(f"unknown layers {unknown} in {tech.id}")
    drawn = set(present)
    segments: list[Segment] = []

    def add(bottom: float, top: float, material: str) -> None:
        if segments and segments[-1].material == material:
            segments[-1] = Segment(segments[-1].z_min_m, top, material)
        else:
            segments.append(Segment(bottom, top, material))

    for level in sorted(tech.stack.levels, key=lambda lv: lv.bottom_m):
        if segments and level.bottom_m - segments[-1].z_max_m > Z_TOLERANCE_M:
            add(segments[-1].z_max_m, level.bottom_m, tech.stack.background)
        pattern = level.pattern
        if isinstance(pattern, Patterned) and drawn.isdisjoint(pattern.layers):
            material = pattern.fill
        else:
            material = level.material
        add(level.bottom_m, level.top_m, material)
    return tuple(segments)
