"""Re-expressing a layout on another database grid."""

from __future__ import annotations

from fractions import Fraction

from pydantic import ValidationError

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.transform import Point
from waffle.contracts.layout.layout import Cell, Instance, InstanceArray, Layout, Port
from waffle.geometry.services.grid import Rounding, round_fraction


class _Scaler:
    """Scales integers by an exact ratio and tallies the ones that need snapping."""

    def __init__(self, ratio: Fraction, rounding: Rounding) -> None:
        self.ratio, self.rounding = ratio, rounding
        self.off_grid = 0
        self.worst = Fraction(0)  # largest snapping error, in new database units

    def __call__(self, value: int) -> int:
        exact = value * self.ratio
        if exact.denominator == 1:
            return int(exact)
        self.off_grid += 1
        if self.rounding is Rounding.EXACT:
            return 0
        scaled = round_fraction(exact, self.rounding)
        self.worst = max(self.worst, abs(scaled - exact))
        return scaled

    def point(self, p: Point) -> Point:
        return (self(p[0]), self(p[1]))


def _scale_shape(shape: Polygon | Rectangle | Path, scale: _Scaler) -> Polygon | Rectangle | Path:
    match shape:
        case Polygon():
            return Polygon(points=tuple(map(scale.point, shape.points)))
        case Rectangle():
            return Rectangle(lo=scale.point(shape.lo), hi=scale.point(shape.hi))
        case Path():
            return Path(
                points=tuple(map(scale.point, shape.points)),
                width=scale(shape.width),
                end=shape.end,
            )


def _scale_instance(instance: Instance, scale: _Scaler) -> Instance:
    transform = instance.transform.model_copy(
        update={"translation": scale.point(instance.transform.translation)}
    )
    array = instance.array
    if array is not None:
        array = InstanceArray(
            columns=array.columns,
            rows=array.rows,
            column_step=scale.point(array.column_step),
            row_step=scale.point(array.row_step),
        )
    return instance.model_copy(update={"transform": transform, "array": array})


def _scale_cell(cell: Cell, scale: _Scaler) -> tuple[Cell, list[str]]:
    """The rescaled cell, and a description of each item that collapsed."""
    collapsed: list[str] = []
    shapes: dict[str, tuple[Polygon | Rectangle | Path, ...]] = {}
    for layer, layer_shapes in cell.shapes.items():
        kept: list[Polygon | Rectangle | Path] = []
        for shape in layer_shapes:
            try:
                kept.append(_scale_shape(shape, scale))
            except ValidationError:
                collapsed.append(f"a {shape.type} on layer {layer!r}")
        shapes[layer] = tuple(kept)
    ports: list[Port] = []
    for port in cell.ports:
        try:
            ports.append(
                Port.model_validate(
                    {
                        **port.model_dump(),
                        "center": scale.point(port.center),
                        "width": scale(port.width),
                    }
                )
            )
        except ValidationError:
            collapsed.append(f"port {port.name!r}")
    instances: list[Instance] = []
    for instance in cell.instances:
        try:
            instances.append(_scale_instance(instance, scale))
        except ValidationError:
            collapsed.append(f"array of instance {instance.name!r}")
    return (
        Cell(name=cell.name, shapes=shapes, instances=tuple(instances), ports=tuple(ports)),
        collapsed,
    )


def regrid(layout: Layout, grid: Grid, rounding: Rounding = Rounding.EXACT) -> Result[Layout]:
    """`layout` on `grid`, with every coordinate and length rescaled by the exact ratio of
    the database units. Names, orientations, hierarchy, and ports' identities are unchanged.
    Array origins and steps are rescaled independently so the placements remain regular.

    Under EXACT, a value that does not land on the new grid is an error
    (`geometry.grid.off_grid`, one diagnostic per cell counting the offenders). Otherwise it
    is snapped under `rounding`, and each cell with snapped values gets a
    `geometry.grid.snapped` warning with the worst error among stored values in picometres.
    A shape, port, or array step that collapses is `geometry.grid.degenerate`.
    """
    ratio = Fraction(layout.grid.dbu_pm, grid.dbu_pm)
    snapping = rounding is not Rounding.EXACT
    found: list[Diagnostic] = []
    cells: list[Cell] = []

    def report(severity: Severity, code: str, message: str, cell: Cell) -> None:
        found.append(
            Diagnostic(
                severity=severity,
                code=code,
                message=message,
                subject=str(layout.id),
                location=("cells", cell.name),
            )
        )

    for cell in layout.cells:
        scale = _Scaler(ratio, rounding)
        scaled, collapsed = _scale_cell(cell, scale)
        cells.append(scaled)
        if snapping or not scale.off_grid:  # under EXACT, off-grid placeholders would mislead
            for what in collapsed:
                report(
                    Severity.ERROR,
                    "geometry.grid.degenerate",
                    f"{what} collapses on the new grid",
                    cell,
                )
        if scale.off_grid and snapping:
            worst = float(scale.worst * grid.dbu_pm)
            report(
                Severity.WARNING,
                "geometry.grid.snapped",
                f"{scale.off_grid} values snapped ({rounding.value}); worst error {worst} pm",
                cell,
            )
        elif scale.off_grid:
            report(
                Severity.ERROR,
                "geometry.grid.off_grid",
                f"{scale.off_grid} values are not on the new {grid.dbu_pm} pm grid",
                cell,
            )
    return Result.of(layout.model_copy(update={"grid": grid, "cells": tuple(cells)}), found)
