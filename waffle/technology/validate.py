"""Check a technology revision for internal consistency.

Codes (all `technology.*`): `grid_mismatch`, `wavelength_range_invalid`,
`duplicate_layer_name`, `duplicate_gds_pair`, `duplicate_material`, `duplicate_level`,
`unknown_material`, `unknown_layer`, `annotation_layer_patterns_stack`, `layer_unmapped`
(warning), `nonpositive_thickness`, `sidewall_angle_invalid`, `sidewall_on_blanket`,
`level_order`, `level_overlap`, `material_validity_gap`, `material_validity_exceeds_table`,
`material_pole_in_range`, plus `units.*` for unknown or mismatched units.
"""

from __future__ import annotations

import math
from collections import Counter

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.units import Dimension
from waffle.contracts.technology.materials import Material, Sellmeier, Tabulated
from waffle.contracts.technology.revision import (
    LayerPurpose,
    Patterned,
    ProcessLevel,
    TechnologyRevision,
)

# Levels closer than this (1 pm) are touching, not overlapping.
Z_TOLERANCE_M = 1e-12


def validate_technology(tech: TechnologyRevision) -> list[Diagnostic]:
    """All problems found in `tech`; empty when it is consistent."""
    found = _Findings(str(tech.id))
    _grid(tech, found)
    found.extend(tech.optical_band.diagnostics(found.subject, ("optical_band",)))
    _layers(tech, found)
    _materials(tech, found)
    _stack(tech, found)
    return found.items


class _Findings:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.items: list[Diagnostic] = []

    def add(self, code: str, message: str, *location: str | int, warning: bool = False) -> None:
        severity = Severity.WARNING if warning else Severity.ERROR
        self.items.append(
            Diagnostic(
                severity=severity,
                code=code,
                message=message,
                subject=self.subject,
                location=location,
            )
        )

    def extend(self, diagnostics: list[Diagnostic]) -> None:
        self.items.extend(diagnostics)


def _grid(tech: TechnologyRevision, found: _Findings) -> None:
    grid = tech.manufacturing_grid_pm
    if grid is not None and grid % tech.dbu_pm:
        found.add(
            "technology.grid_mismatch",
            f"manufacturing grid {grid} pm is not a multiple of the database unit {tech.dbu_pm} pm",
            "manufacturing_grid_pm",
        )


def _layers(tech: TechnologyRevision, found: _Findings) -> None:
    names: set[str] = set()
    pairs: dict[tuple[int, int], str] = {}
    for i, layer in enumerate(tech.layers):
        if layer.name in names:
            found.add(
                "technology.duplicate_layer_name",
                f"layer name {layer.name!r} is defined more than once",
                "layers",
                i,
                "name",
            )
        names.add(layer.name)
        pair = (layer.gds_layer, layer.gds_datatype)
        if pair in pairs:
            found.add(
                "technology.duplicate_gds_pair",
                f"GDS pair {pair[0]}/{pair[1]} is used by {pairs[pair]!r} and {layer.name!r}",
                "layers",
                i,
            )
        pairs.setdefault(pair, layer.name)


def _materials(tech: TechnologyRevision, found: _Findings) -> None:
    for name, count in Counter(m.name for m in tech.materials).items():
        if count > 1:
            found.add(
                "technology.duplicate_material", f"material {name!r} is defined {count} times"
            )
    used = {tech.stack.background}
    for level in tech.stack.levels:
        used.add(level.material)
        if isinstance(level.pattern, Patterned):
            used.add(level.pattern.fill)
    for i, material in enumerate(tech.materials):
        range_problems = material.validity.diagnostics(found.subject, ("materials", i, "validity"))
        found.extend(range_problems)
        if range_problems:
            continue
        _model(material, i, found)
        if material.name in used and not material.validity.covers(tech.optical_band):
            found.add(
                "technology.material_validity_gap",
                f"material {material.name!r} is not valid over the whole optical band",
                "materials",
                i,
                "validity",
            )


def _model(material: Material, index: int, found: _Findings) -> None:
    model = material.optical
    low, high = material.validity.minimum.si * 1e6, material.validity.maximum.si * 1e6
    if isinstance(model, Sellmeier):
        for c in model.c_um2:
            if c > 0 and low <= math.sqrt(c) <= high:
                found.add(
                    "technology.material_pole_in_range",
                    f"Sellmeier resonance at {math.sqrt(c):g} um lies inside the validity range",
                    "materials",
                    index,
                    "optical",
                )
    elif isinstance(model, Tabulated) and not model.span.covers(material.validity):
        found.add(
            "technology.material_validity_exceeds_table",
            "validity range extends beyond the tabulated wavelengths",
            "materials",
            index,
            "validity",
        )


def _stack(tech: TechnologyRevision, found: _Findings) -> None:
    material_names = {m.name for m in tech.materials}

    def check_material(name: str, *location: str | int) -> None:
        if name not in material_names:
            found.add("technology.unknown_material", f"unknown material {name!r}", *location)

    check_material(tech.stack.background, "stack", "background")
    for name, count in Counter(lv.name for lv in tech.stack.levels).items():
        if count > 1:
            found.add("technology.duplicate_level", f"level {name!r} is defined {count} times")
    patterned: set[str] = set()
    geometry_ok = True
    for i, level in enumerate(tech.stack.levels):
        where = ("stack", "levels", i)
        check_material(level.material, *where, "material")
        geometry_ok &= _level_geometry(level, found, where)
        if isinstance(level.pattern, Patterned):
            check_material(level.pattern.fill, *where, "pattern", "fill")
            for j, layer_name in enumerate(level.pattern.layers):
                layer = tech.layer(layer_name)
                if layer is None:
                    found.add(
                        "technology.unknown_layer",
                        f"level {level.name!r} uses layer {layer_name!r}, which is not in the "
                        "layer table",
                        *where,
                        "pattern",
                        "layers",
                        j,
                    )
                elif layer.purpose is LayerPurpose.ANNOTATION:
                    found.add(
                        "technology.annotation_layer_patterns_stack",
                        f"annotation layer {layer_name!r} cannot pattern level {level.name!r}",
                        *where,
                        "pattern",
                        "layers",
                        j,
                    )
                patterned.add(layer_name)
    for i, layer in enumerate(tech.layers):
        if layer.purpose is LayerPurpose.DRAWING and layer.name not in patterned:
            found.add(
                "technology.layer_unmapped",
                f"drawing layer {layer.name!r} patterns no stack level",
                "layers",
                i,
                warning=True,
            )
    if geometry_ok:
        _vertical_order(tech.stack.levels, found)


def _level_geometry(level: ProcessLevel, found: _Findings, where: tuple[str | int, ...]) -> bool:
    """Check units, thickness, and sidewall of one level; True when its z range is usable."""
    problems = [
        *level.z_min.diagnostics(
            Dimension.LENGTH, subject=found.subject, location=(*where, "z_min")
        ),
        *level.thickness.diagnostics(
            Dimension.LENGTH, subject=found.subject, location=(*where, "thickness")
        ),
    ]
    found.extend(problems)
    usable = not problems
    if usable and level.thickness.si <= 0:
        found.add(
            "technology.nonpositive_thickness",
            f"level {level.name!r} has thickness {level.thickness.value:g} {level.thickness.unit}",
            *where,
            "thickness",
        )
        usable = False
    angle = level.sidewall_angle
    if angle is not None:
        angle_problems = angle.diagnostics(
            Dimension.ANGLE, subject=found.subject, location=(*where, "sidewall_angle")
        )
        found.extend(angle_problems)
        if not isinstance(level.pattern, Patterned):
            found.add(
                "technology.sidewall_on_blanket",
                f"blanket level {level.name!r} has no sidewalls",
                *where,
                "sidewall_angle",
            )
        elif not angle_problems and not 0 < angle.to("deg").value <= 90:
            found.add(
                "technology.sidewall_angle_invalid",
                "sidewall angle must be in (0, 90] degrees",
                *where,
                "sidewall_angle",
            )
    return usable


def _vertical_order(levels: tuple[ProcessLevel, ...], found: _Findings) -> None:
    for i, upper in enumerate(levels):
        for j in range(i):
            lower = levels[j]
            if upper.bottom_m < lower.top_m - Z_TOLERANCE_M and (
                lower.bottom_m < upper.top_m - Z_TOLERANCE_M
            ):
                found.add(
                    "technology.level_overlap",
                    f"levels {lower.name!r} and {upper.name!r} overlap in z",
                    "stack",
                    "levels",
                    i,
                )
        if i and upper.bottom_m < levels[i - 1].bottom_m - Z_TOLERANCE_M:
            found.add(
                "technology.level_order",
                f"level {upper.name!r} starts below the level listed before it",
                "stack",
                "levels",
                i,
            )
