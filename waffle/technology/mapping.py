"""Layer mapping queries: which stack levels does a layout layer pattern?

Outcomes are diagnostics, never silent picks:

* `technology.layer_unknown`: no layer with that name or GDS pair (missing mapping).
* `technology.layer_ambiguous`: several table entries match the name or GDS pair.
* `technology.layer_unmapped`: a drawing layer that patterns no level.

An annotation layer maps to no levels by design and is not an error. A layer patterning
several levels (a rib waveguide's slab and core) is the normal case, not an ambiguity; the
levels are returned bottom to top. Overlapping levels are `validate_technology`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.technology.revision import (
    LayerPurpose,
    LayoutLayer,
    Patterned,
    ProcessLevel,
    TechnologyRevision,
)


@dataclass(frozen=True)
class LayerMapping:
    layer: LayoutLayer
    levels: tuple[ProcessLevel, ...]


def map_layer(tech: TechnologyRevision, name: str) -> Result[LayerMapping]:
    """The levels patterned by the logical layer `name`."""
    matches = [layer for layer in tech.layers if layer.name == name]
    return _mapping(tech, matches, f"name {name!r}")


def map_gds(tech: TechnologyRevision, gds_layer: int, gds_datatype: int) -> Result[LayerMapping]:
    """The levels patterned by the layer drawn on GDS `gds_layer/gds_datatype`."""
    matches = [
        layer
        for layer in tech.layers
        if (layer.gds_layer, layer.gds_datatype) == (gds_layer, gds_datatype)
    ]
    return _mapping(tech, matches, f"GDS pair {gds_layer}/{gds_datatype}")


def _mapping(
    tech: TechnologyRevision, matches: list[LayoutLayer], query: str
) -> Result[LayerMapping]:
    subject = str(tech.id)
    if not matches:
        return _fail("technology.layer_unknown", f"no layer with {query}", subject)
    if len(matches) > 1:
        names = ", ".join(repr(m.name) for m in matches)
        return _fail("technology.layer_ambiguous", f"{query} matches layers {names}", subject)
    layer = matches[0]
    levels = tuple(
        level
        for level in tech.stack.levels
        if isinstance(level.pattern, Patterned) and layer.name in level.pattern.layers
    )
    if not levels and layer.purpose is LayerPurpose.DRAWING:
        return _fail(
            "technology.layer_unmapped",
            f"drawing layer {layer.name!r} patterns no stack level",
            subject,
        )
    return Result.of(LayerMapping(layer, levels))


def _fail(code: str, message: str, subject: str) -> Result[LayerMapping]:
    return Result(
        None, (Diagnostic(severity=Severity.ERROR, code=code, message=message, subject=subject),)
    )
