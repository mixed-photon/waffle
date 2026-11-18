"""Flattening a cell hierarchy into per-layer polygons and hierarchically named ports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.shapes import BBox, Polygon
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import Cell, Layout
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.services.cells import QualifiedPort
from waffle.layout.services.validate import validate_layout


@dataclass(frozen=True)
class FlatCell:
    """A cell with its whole hierarchy expanded into its own frame.

    `polygons` maps logical layer names to polygons (rectangles and paths expanded),
    sorted; overlapping polygons are kept as they are, not merged. `ports` holds every port
    of the cell and of every nested instance, sorted by path (`name` for the cell's own
    ports, `inst/name` and deeper `a/b/name` for instance ports).
    """

    cell: str
    polygons: dict[str, tuple[Polygon, ...]]
    ports: tuple[QualifiedPort, ...]

    def bounding_box(self) -> BBox | None:
        return BBox.of(p for polys in self.polygons.values() for poly in polys for p in poly.points)


def flatten(layout: Layout, cell: str | None = None) -> Result[FlatCell]:
    """Flatten `cell` (default: the top cell), composing instance transforms exactly.

    The layout must pass `validate_layout` (without the self-intersection test); otherwise
    its diagnostics are returned.
    """
    found = validate_layout(layout, check_simple=False)
    name = layout.top if cell is None else cell
    cells: dict[str, Cell] = {c.name: c for c in layout.cells}
    if name not in cells:
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="layout.cell.unknown",
                message=f"cell {name!r} is not defined",
                subject=str(layout.id),
                location=("cells", name),
            )
        )
    if has_errors(found):
        return Result(None, tuple(found))

    polygons: defaultdict[str, list[Polygon]] = defaultdict(list)
    ports: list[QualifiedPort] = []

    def walk(cell_name: str, transform: Transform, prefix: str) -> None:
        current = cells[cell_name]
        for layer, shapes in current.shapes.items():
            polygons[layer].extend(to_polygon(s).unwrap().transformed(transform) for s in shapes)
        ports.extend(
            QualifiedPort(prefix + p.name, p.transformed(transform)) for p in current.ports
        )
        for instance in current.instances:
            walk(instance.cell, transform.compose(instance.transform), f"{prefix}{instance.name}/")

    walk(name, Transform(), "")
    return Result.of(
        FlatCell(
            cell=name,
            polygons={
                layer: tuple(sorted(polys, key=lambda poly: poly.points))
                for layer, polys in sorted(polygons.items())
            },
            ports=tuple(sorted(ports, key=lambda q: q.path)),
        )
    )
