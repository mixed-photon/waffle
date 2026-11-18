"""Operations on single cells and instances that keep port identity."""

from __future__ import annotations

from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import Cell, Instance, Layout, Port


@dataclass(frozen=True)
class QualifiedPort:
    """A port reached through a path of instance names, e.g. `m1/l1/o1`.

    `port` is expressed in the frame where the path starts; its `name` is the leaf name.
    A top-level port's path is just its name.
    """

    path: str
    port: Port


def transform_cell(cell: Cell, transform: Transform) -> Cell:
    """`cell` moved by `transform`: shapes, ports, and instance transforms, all names kept."""
    return Cell(
        name=cell.name,
        shapes={
            layer: tuple(shape.transformed(transform) for shape in shapes)
            for layer, shapes in cell.shapes.items()
        },
        instances=tuple(
            i.model_copy(update={"transform": transform.compose(i.transform)})
            for i in cell.instances
        ),
        ports=tuple(p.transformed(transform) for p in cell.ports),
    )


def instance_ports(layout: Layout, instance: Instance) -> Result[tuple[QualifiedPort, ...]]:
    """The child cell's own ports in the parent's frame, named `<instance>/<port>`."""
    child = next((c for c in layout.cells if c.name == instance.cell), None)
    if child is None:
        return Result(
            None,
            (
                Diagnostic(
                    severity=Severity.ERROR,
                    code="layout.instance.unknown_cell",
                    message=f"unknown cell {instance.cell!r}",
                    subject=str(layout.id),
                    location=("instances", instance.name),
                ),
            ),
        )
    return Result.of(
        tuple(
            QualifiedPort(f"{instance.name}/{p.name}", p.transformed(instance.transform))
            for p in child.ports
        )
    )
