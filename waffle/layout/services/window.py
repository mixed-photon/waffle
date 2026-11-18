"""Select whole polygons and ports in a layout window without flattening the design."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.shapes import BBox, Polygon
from waffle.contracts.geometry.transform import Point, Transform
from waffle.contracts.layout.layout import Cell, Instance, InstanceArray, Layout
from waffle.geometry.services.shapes import polygon_intersects_box, to_polygon
from waffle.layout.services.cells import QualifiedPort
from waffle.layout.services.validate import validate_layout


@dataclass(frozen=True)
class LayoutWindow:
    """Geometry touching a closed window, expressed in the selected cell's frame.

    Polygons keep their original outlines, including any part outside the window.
    Ports are selected by center and retain their paths through the hierarchy.
    """

    cell: str
    window: BBox
    polygons: dict[str, tuple[Polygon, ...]]
    ports: tuple[QualifiedPort, ...]


@dataclass(frozen=True)
class _CellGeometry:
    polygons: tuple[tuple[str, Polygon, BBox], ...]
    bounds: BBox | None


def _array_bounds(base: BBox, array: InstanceArray) -> BBox:
    cx, cy = array.column_step
    rx, ry = array.row_step
    dx = (array.columns - 1) * cx + (array.rows - 1) * rx
    dy = (array.columns - 1) * cy + (array.rows - 1) * ry
    return BBox(
        (base.lo[0] + min(0, dx), base.lo[1] + min(0, dy)),
        (base.hi[0] + max(0, dx), base.hi[1] + max(0, dy)),
    )


def _index_cell(
    name: str, cells: dict[str, Cell], index: dict[str, _CellGeometry]
) -> _CellGeometry:
    if name in index:
        return index[name]
    cell = cells[name]
    polygons: list[tuple[str, Polygon, BBox]] = []
    extent = [p.center for p in cell.ports]
    for layer, shapes in cell.shapes.items():
        for shape in shapes:
            polygon = to_polygon(shape).unwrap()
            bounds = BBox.of(polygon.points)
            assert bounds is not None
            polygons.append((layer, polygon, bounds))
            extent.extend((bounds.lo, bounds.hi))
    for instance in cell.instances:
        child = _index_cell(instance.cell, cells, index)
        if child.bounds is not None:
            base = child.bounds.transformed(instance.transform)
            bounds = (
                base if instance.array is None else _array_bounds(base, instance.array)
            )
            extent.extend((bounds.lo, bounds.hi))
    geometry = _CellGeometry(tuple(polygons), BBox.of(extent))
    index[name] = geometry
    return geometry


def _indices(step: Point, count: int, base: BBox, window: BBox) -> range:
    """Solve the overlap inequalities along the axis of a signed step."""
    if count == 1:
        return range(1)
    axis = 0 if step[0] else 1
    pitch = step[axis]
    lo = window.lo[axis] - base.hi[axis]
    hi = window.hi[axis] - base.lo[axis]
    if pitch < 0:
        lo, hi, pitch = -hi, -lo, -pitch
    return range(max(0, -(-lo // pitch)), min(count, hi // pitch + 1))


def _placements_in_window(
    instance: Instance, bounds: BBox, window: BBox
) -> Iterator[tuple[str, Transform]]:
    base = bounds.transformed(instance.transform)
    array = instance.array
    if array is None:
        if base.intersects(window):
            yield instance.placement(0, 0)
        return
    if not _array_bounds(base, array).intersects(window):
        return
    columns = _indices(array.column_step, array.columns, base, window)
    rows = _indices(array.row_step, array.rows, base, window)
    for row in rows:
        for column in columns:
            yield instance.placement(column, row)


def extract_window(
    layout: Layout, window: BBox, cell: str | None = None
) -> Result[LayoutWindow]:
    """Select a window from a valid layout, defaulting to the top cell.

    Bounds are calculated once per reachable cell definition. Array ranges are
    computed directly from the window, so even very large disjoint arrays stay
    compact. This query selects geometry for later processing or visualization.
    """
    found = validate_layout(layout, check_simple=False)
    name = layout.top if cell is None else cell
    cells = {entry.name: entry for entry in layout.cells}
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
    if any(lo > hi for lo, hi in zip(window.lo, window.hi, strict=True)):
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="layout.window.invalid",
                message="window bounds must be ordered in both axes",
                subject=str(layout.id),
                location=("window",),
            )
        )
    if has_errors(found):
        return Result(None, tuple(found))

    index: dict[str, _CellGeometry] = {}
    _index_cell(name, cells, index)
    polygons: defaultdict[str, list[Polygon]] = defaultdict(list)
    ports: list[QualifiedPort] = []

    def walk(
        current: str, transform: Transform, local_window: BBox, prefix: str
    ) -> None:
        for layer, polygon, bounds in index[current].polygons:
            if bounds.intersects(local_window) and polygon_intersects_box(
                polygon, local_window
            ):
                polygons[layer].append(polygon.transformed(transform))
        cell = cells[current]
        ports.extend(
            QualifiedPort(prefix + port.name, port.transformed(transform))
            for port in cell.ports
            if local_window.contains(port.center)
        )
        for instance in cell.instances:
            child_bounds = index[instance.cell].bounds
            if child_bounds is None:
                continue
            for placement_name, placement in _placements_in_window(
                instance, child_bounds, local_window
            ):
                walk(
                    instance.cell,
                    transform.compose(placement),
                    local_window.transformed(placement.inverse()),
                    f"{prefix}{placement_name}/",
                )

    walk(name, Transform(), window, "")
    return Result.of(
        LayoutWindow(
            cell=name,
            window=window,
            polygons={
                layer: tuple(sorted(polys, key=lambda poly: poly.points))
                for layer, polys in sorted(polygons.items())
            },
            ports=tuple(sorted(ports, key=lambda port: port.path)),
        )
    )
