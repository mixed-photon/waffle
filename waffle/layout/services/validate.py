"""Whole-layout validation: identities, references, hierarchy, layers, and shapes."""

from __future__ import annotations

from collections import Counter

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Point
from waffle.contracts.layout.layout import Cell, Layout
from waffle.geometry.services.shapes import validate_shape

Location = tuple[str | int, ...]


def _points(shape: Polygon | Rectangle | Path) -> tuple[Point, ...]:
    return (shape.lo, shape.hi) if isinstance(shape, Rectangle) else shape.points


def find_cycles(cells: dict[str, Cell]) -> list[list[str]]:
    """Each instance cycle once, as the cell names along it, first name repeated at the end."""
    state: dict[str, bool] = {}  # False while on the stack, True once finished
    stack: list[str] = []
    found: dict[tuple[str, ...], None] = {}

    def visit(name: str) -> None:
        state[name] = False
        stack.append(name)
        for instance in cells[name].instances:
            child = instance.cell
            if child not in cells:
                continue
            if state.get(child) is False:
                found[(*stack[stack.index(child) :], child)] = None
            elif child not in state:
                visit(child)
        stack.pop()
        state[name] = True

    for name in sorted(cells):
        if name not in state:
            visit(name)
    return [list(cycle) for cycle in found]


def validate_layout(layout: Layout, *, check_simple: bool = True) -> list[Diagnostic]:
    """Every problem in `layout`; an empty list means it is well formed.

    Checks, with codes: duplicate layer names or pairs (`layout.layer.duplicate_name`,
    `layout.layer.duplicate_pair`), duplicate cells, instances, and ports
    (`layout.cell.duplicate`, `layout.instance.duplicate`, `layout.port.duplicate`), unknown
    top or instance cells (`layout.top.unknown`, `layout.instance.unknown_cell`), recursive
    hierarchy (`layout.hierarchy.cycle`), shapes or ports on layers missing from the table
    (`layout.shape.unknown_layer`, `layout.port.unknown_layer`), points off the manufacturing
    grid (`layout.grid.off_manufacturing`), and invalid shapes (see
    `waffle.geometry.services.shapes.validate_shape`). `check_simple=False` skips the
    quadratic self-intersection test.
    """
    subject = str(layout.id)
    found: list[Diagnostic] = []

    def error(code: str, message: str, location: Location) -> None:
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                message=message,
                subject=subject,
                location=location,
            )
        )

    for kind, counts in (
        ("name", Counter(spec.name for spec in layout.layers)),
        ("pair", Counter((spec.layer, spec.datatype) for spec in layout.layers)),
    ):
        for key, count in counts.items():
            if count > 1:
                error(
                    f"layout.layer.duplicate_{kind}",
                    f"layer {key} is defined {count} times",
                    ("layers",),
                )
    layer_names = {spec.name for spec in layout.layers}

    for name, count in Counter(cell.name for cell in layout.cells).items():
        if count > 1:
            error(
                "layout.cell.duplicate", f"cell {name!r} is defined {count} times", ("cells", name)
            )
    cells = {cell.name: cell for cell in reversed(layout.cells)}  # first definition wins
    if layout.top not in cells:
        error("layout.top.unknown", f"top cell {layout.top!r} is not defined", ("top",))

    grid = layout.grid
    for cell in layout.cells:
        here: Location = ("cells", cell.name)

        for name, count in Counter(i.name for i in cell.instances).items():
            if count > 1:
                error("layout.instance.duplicate", f"instance {name!r} appears {count} times", here)
        for instance in cell.instances:
            at = (*here, "instances", instance.name)
            if instance.cell not in cells:
                error("layout.instance.unknown_cell", f"unknown cell {instance.cell!r}", at)
            if not all(grid.on_manufacturing(v) for v in instance.transform.translation):
                error("layout.grid.off_manufacturing", "translation is off the grid", at)

        for name, count in Counter(p.name for p in cell.ports).items():
            if count > 1:
                error("layout.port.duplicate", f"port {name!r} appears {count} times", here)
        for port in cell.ports:
            at = (*here, "ports", port.name)
            if port.layer not in layer_names:
                error("layout.port.unknown_layer", f"layer {port.layer!r} is not in the table", at)
            if not all(grid.on_manufacturing(v) for v in port.center):
                error("layout.grid.off_manufacturing", "port center is off the grid", at)

        for layer, shapes in cell.shapes.items():
            if layer not in layer_names:
                error(
                    "layout.shape.unknown_layer",
                    f"layer {layer!r} is not in the table",
                    (*here, "shapes", layer),
                )
            for j, shape in enumerate(shapes):
                at = (*here, "shapes", layer, j)
                found.extend(
                    validate_shape(shape, check_simple=check_simple, subject=subject, location=at)
                )
                if not all(grid.on_manufacturing(v) for p in _points(shape) for v in p):
                    error("layout.grid.off_manufacturing", "vertex is off the grid", at)

    for cycle in find_cycles(cells):
        error(
            "layout.hierarchy.cycle",
            "recursive hierarchy: " + " -> ".join(cycle),
            ("cells", cycle[0]),
        )
    return found
