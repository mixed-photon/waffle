"""A hash of the physically relevant content of a technology revision.

`revision_of(tech)` changes with any edit, descriptive text included. `physical_fingerprint`
changes only when something a simulation or mapping depends on changes (MOD-018).

Included: grid (`dbu_pm`, `manufacturing_grid_pm`), `optical_band`; each layer's name, GDS
pair, and purpose; each material's name, optical model numbers, and validity range; the
stack's background and each level's name, z range, material, pattern (kind, layers, fill),
and sidewall angle. Lengths and angles are converted to SI and rounded to 12 significant
digits, so `0.22 um` and `220 nm` agree. Layers and materials are order-independent; the
order of levels is kept.

Excluded: the technology `id`, `metadata`, `schema_version`, `LayoutLayer.description`,
and `Material.source`.
"""

from __future__ import annotations

from typing import Any

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import Material, WavelengthRange
from waffle.contracts.technology.revision import Patterned, ProcessLevel, TechnologyRevision


def physical_fingerprint(tech: TechnologyRevision) -> str:
    """`sha256:<hex>` over the physical content of `tech`. Quantities need known units."""
    return content_hash(
        {
            "dbu_pm": tech.dbu_pm,
            "manufacturing_grid_pm": tech.manufacturing_grid_pm,
            "optical_band": _range(tech.optical_band),
            "layers": sorted(
                (
                    {
                        "name": layer.name,
                        "gds": [layer.gds_layer, layer.gds_datatype],
                        "purpose": layer.purpose.value,
                    }
                    for layer in tech.layers
                ),
                key=lambda layer: layer["name"],
            ),
            "materials": sorted((_material(m) for m in tech.materials), key=lambda m: m["name"]),
            "background": tech.stack.background,
            "levels": [_level(level) for level in tech.stack.levels],
        }
    )


def _si(quantity: Quantity) -> float:
    return float(f"{quantity.si:.12g}")


def _range(wavelengths: WavelengthRange) -> list[float]:
    return [_si(wavelengths.minimum), _si(wavelengths.maximum)]


def _material(material: Material) -> dict[str, Any]:
    return {
        "name": material.name,
        "optical": material.optical.model_dump(mode="json"),
        "validity": _range(material.validity),
    }


def _level(level: ProcessLevel) -> dict[str, Any]:
    pattern: dict[str, Any] = {"type": level.pattern.type}
    if isinstance(level.pattern, Patterned):
        pattern |= {"layers": sorted(level.pattern.layers), "fill": level.pattern.fill}
    angle = level.sidewall_angle
    return {
        "name": level.name,
        "z_min": _si(level.z_min),
        "thickness": _si(level.thickness),
        "material": level.material,
        "pattern": pattern,
        "sidewall_rad": None if angle is None else _si(angle),
    }
