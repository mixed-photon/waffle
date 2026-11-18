"""Flattened geometry in physical units, for layout-to-physics conversion."""

from __future__ import annotations

from dataclasses import dataclass

from waffle.contracts.component.definition import Domain
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.transform import Orientation
from waffle.geometry.services.grid import dbu_to_float
from waffle.layout.services.flatten import FlatCell

FloatPoint = tuple[float, float]


@dataclass(frozen=True)
class PhysicalPort:
    """A flattened port with lengths as floats in the cell's `unit`."""

    path: str
    center: FloatPoint
    width: float
    orientation: Orientation
    layer: str
    domain: Domain


@dataclass(frozen=True)
class PhysicalCell:
    """`polygons` are counter-clockwise vertex rings per logical layer; all lengths are
    floats in `unit` (default micrometres), each the correctly rounded exact value of its
    integer coordinate. Convert back with the layout's grid to recover the integers."""

    cell: str
    unit: str
    polygons: dict[str, tuple[tuple[FloatPoint, ...], ...]]
    ports: tuple[PhysicalPort, ...]


def to_physical(flat: FlatCell, grid: Grid, unit: str = "um") -> PhysicalCell:
    def length(value: int) -> float:
        return dbu_to_float(value, grid, unit)

    return PhysicalCell(
        cell=flat.cell,
        unit=unit,
        polygons={
            layer: tuple(tuple((length(x), length(y)) for x, y in poly.points) for poly in polys)
            for layer, polys in flat.polygons.items()
        },
        ports=tuple(
            PhysicalPort(
                path=q.path,
                center=(length(q.port.center[0]), length(q.port.center[1])),
                width=length(q.port.width),
                orientation=q.port.orientation,
                layer=q.port.layer,
                domain=q.port.domain,
            )
            for q in flat.ports
        ),
    )
