"""Layout IR: a library of cells with shapes, instances, and ports on a database grid.

Identities are names: cell names are unique in a layout, instance and port names in a cell.
All names match `NAME` (no `/`, which separates the parts of a flattened port path).
Shapes are grouped by **logical layer name**; the layer table maps each name to its
`(layer, datatype)` pair. Instance hierarchy and the layer table are checked by
`waffle.layout.services.validate.validate_layout`, since they need the whole layout.

Everything that is a set by meaning is stored sorted (layers, cells, instances, ports by
name; shapes by `shape_key`), so equal layouts serialize identically and share a revision.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.records import Record
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Shape, shape_key
from waffle.contracts.geometry.transform import Orientation, Point, Transform

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*$")]


class LayerSpec(BaseModel):
    """A logical layer name and its GDS `(layer, datatype)` pair; both are unique per layout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Name
    layer: StrictInt = Field(ge=0, le=65535)
    datatype: StrictInt = Field(ge=0, le=65535)


class Port(BaseModel):
    """A physical connection point of a cell.

    `center` and `width` are in dbu. `orientation` is the direction the port faces, as a
    counter-clockwise angle from +x in the cell's frame (0 faces +x, 90 faces +y); for an
    optical port it points out of the component, so mating ports face opposite ways.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Name
    center: Point
    width: StrictInt = Field(gt=0)
    orientation: Orientation
    layer: Name
    domain: Domain

    def transformed(self, transform: Transform) -> Port:
        """The same port (same name, layer, domain, width) seen through `transform`."""
        return self.model_copy(
            update={
                "center": transform.apply(self.center),
                "orientation": transform.apply_direction(self.orientation),
            }
        )


class Instance(BaseModel):
    """One placement of the cell named `cell`; `transform` maps child to parent frame."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Name
    cell: Name
    transform: Transform = Transform()


class Cell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Name
    shapes: dict[str, tuple[Shape, ...]] = Field(default_factory=dict)
    instances: tuple[Instance, ...] = ()
    ports: tuple[Port, ...] = ()

    @field_validator("shapes")
    @classmethod
    def _sort_shapes(cls, shapes: dict[str, tuple[Shape, ...]]) -> dict[str, tuple[Shape, ...]]:
        return {k: tuple(sorted(v, key=shape_key)) for k, v in sorted(shapes.items())}

    @field_validator("instances")
    @classmethod
    def _sort_instances(cls, instances: tuple[Instance, ...]) -> tuple[Instance, ...]:
        return tuple(sorted(instances, key=lambda item: item.name))

    @field_validator("ports")
    @classmethod
    def _sort_ports(cls, ports: tuple[Port, ...]) -> tuple[Port, ...]:
        return tuple(sorted(ports, key=lambda item: item.name))


class Layout(Record):
    """A cell library with a designated top cell.

    `top` names the cell that represents the layout; other cells are reachable only
    through instances. Hierarchy and layer references are validated, not enforced here.
    """

    kind: Literal["layout"] = "layout"
    schema_version: Literal[1] = 1
    grid: Grid
    top: Name
    layers: tuple[LayerSpec, ...] = ()
    cells: tuple[Cell, ...]

    @field_validator("layers")
    @classmethod
    def _sort_layers(cls, layers: tuple[LayerSpec, ...]) -> tuple[LayerSpec, ...]:
        return tuple(sorted(layers, key=lambda spec: spec.name))

    @field_validator("cells")
    @classmethod
    def _sort_cells(cls, cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
        return tuple(sorted(cells, key=lambda cell: cell.name))
