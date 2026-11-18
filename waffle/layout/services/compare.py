"""Semantic comparison of two layouts, independent of storage order and descriptive fields.

Ignored: record ids, metadata, the manufacturing grid, and the order of cells, shapes,
instances, ports, and layers. Shapes are compared as normalized polygons, so a rectangle
equals the polygon with its four corners, and a path equals its outline. Polygons are
compared as multisets of exact rings; overlapping or abutting polygons are not merged, so a
rectangle split in two differs from the whole.

* `HIERARCHICAL`: same layer table, top name, cell names, and per cell the same shapes,
  instances (name, child cell, transform, array), and ports.
* `FLAT`: same layer table and the same flattened per-layer polygons and port paths and
  fields of the two top cells, whatever the hierarchy and cell or instance names inside.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.layout.layout import Cell, Layout
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.services.flatten import flatten
from waffle.layout.services.validate import validate_layout

Location = tuple[str | int, ...]


class CompareMode(StrEnum):
    HIERARCHICAL = "hierarchical"
    FLAT = "flat"


def _diff(code: str, message: str, location: Location) -> Diagnostic:
    return Diagnostic(severity=Severity.ERROR, code=code, message=message, location=location)


def _compare_keyed[V](
    code: str, what: str, a: Mapping[str, V], b: Mapping[str, V], location: Location
) -> list[Diagnostic]:
    """Differences between two name-keyed collections: missing, extra, and changed members."""
    found = [
        _diff(code, f"{what} {name!r} only in the first layout", (*location, name))
        for name in sorted(a.keys() - b.keys())
    ]
    found += [
        _diff(code, f"{what} {name!r} only in the second layout", (*location, name))
        for name in sorted(b.keys() - a.keys())
    ]
    found += [
        _diff(code, f"{what} {name!r} differs: {a[name]} vs {b[name]}", (*location, name))
        for name in sorted(a.keys() & b.keys())
        if a[name] != b[name]
    ]
    return found


def _compare_polygons(a: list[Polygon], b: list[Polygon], location: Location) -> list[Diagnostic]:
    ca, cb = Counter(p.points for p in a), Counter(p.points for p in b)
    only_a, only_b = sum((ca - cb).values()), sum((cb - ca).values())
    if not (only_a or only_b):
        return []
    return [
        _diff(
            "layout.diff.shapes",
            f"{only_a} polygons only in the first layout, {only_b} only in the second",
            location,
        )
    ]


def _polygons(shapes: tuple[Polygon | Rectangle | Path, ...]) -> list[Polygon]:
    return [to_polygon(shape).unwrap() for shape in shapes]


def _compare_cell(a: Cell, b: Cell) -> list[Diagnostic]:
    here: Location = ("cells", a.name)
    found: list[Diagnostic] = []
    for layer in sorted(a.shapes.keys() | b.shapes.keys()):
        found += _compare_polygons(
            _polygons(a.shapes.get(layer, ())),
            _polygons(b.shapes.get(layer, ())),
            (*here, "shapes", layer),
        )
    found += _compare_keyed(
        "layout.diff.instance",
        "instance",
        {i.name: (i.cell, i.transform, i.array) for i in a.instances},
        {i.name: (i.cell, i.transform, i.array) for i in b.instances},
        (*here, "instances"),
    )
    found += _compare_keyed(
        "layout.diff.port",
        "port",
        {p.name: p for p in a.ports},
        {p.name: p for p in b.ports},
        (*here, "ports"),
    )
    return found


def compare_layouts(
    a: Layout, b: Layout, mode: CompareMode = CompareMode.HIERARCHICAL
) -> list[Diagnostic]:
    """Differences between two layouts as `layout.diff.*` errors; empty means equivalent.

    Codes: `layout.diff.grid` (different database units; nothing else is compared),
    `layout.diff.layer`, `layout.diff.top`, `layout.diff.cell`, `layout.diff.shapes`,
    `layout.diff.instance`, `layout.diff.port`. Layouts that fail `validate_layout` are
    reported with their own diagnostics instead, each message prefixed by which layout.
    """
    invalid = [
        d.model_copy(update={"message": f"{label} layout: {d.message}"})
        for label, layout in (("first", a), ("second", b))
        for d in validate_layout(layout, check_simple=False)
    ]
    if invalid:
        return invalid
    if a.grid.dbu_pm != b.grid.dbu_pm:
        return [
            _diff(
                "layout.diff.grid",
                f"database units differ: {a.grid.dbu_pm} pm vs {b.grid.dbu_pm} pm",
                ("grid",),
            )
        ]
    found = _compare_keyed(
        "layout.diff.layer",
        "layer",
        {s.name: (s.layer, s.datatype) for s in a.layers},
        {s.name: (s.layer, s.datatype) for s in b.layers},
        ("layers",),
    )
    if mode is CompareMode.FLAT:
        flat_a, flat_b = flatten(a).unwrap(), flatten(b).unwrap()
        for layer in sorted(flat_a.polygons.keys() | flat_b.polygons.keys()):
            found += _compare_polygons(
                list(flat_a.polygons.get(layer, ())),
                list(flat_b.polygons.get(layer, ())),
                ("flat", "shapes", layer),
            )
        found += _compare_keyed(
            "layout.diff.port",
            "port",
            {q.path: q.port for q in flat_a.ports},
            {q.path: q.port for q in flat_b.ports},
            ("flat", "ports"),
        )
        return found
    if a.top != b.top:
        found.append(
            _diff("layout.diff.top", f"top cells differ: {a.top!r} vs {b.top!r}", ("top",))
        )
    cells_a, cells_b = {c.name: c for c in a.cells}, {c.name: c for c in b.cells}
    found += [
        _diff("layout.diff.cell", f"cell {name!r} only in the {side} layout", ("cells", name))
        for side, only in (
            ("first", cells_a.keys() - cells_b.keys()),
            ("second", cells_b.keys() - cells_a.keys()),
        )
        for name in sorted(only)
    ]
    for name in sorted(cells_a.keys() & cells_b.keys()):
        found += _compare_cell(cells_a[name], cells_b[name])
    return found
