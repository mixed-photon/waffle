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

from collections.abc import Iterator
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

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


class InstanceArray(BaseModel):
    """A rectangular array with signed step vectors in the parent frame, in dbu."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: StrictInt = Field(ge=1)
    rows: StrictInt = Field(ge=1)
    column_step: Point
    row_step: Point

    @model_validator(mode="after")
    def _validate_steps(self) -> Self:
        if self.columns == self.rows == 1:
            raise ValueError("an instance array must contain more than one placement")
        for axis, count, (x, y) in (
            ("column", self.columns, self.column_step),
            ("row", self.rows, self.row_step),
        ):
            if count == 1:
                if x or y:
                    raise ValueError(f"{axis} step must be zero when its count is one")
            elif bool(x) == bool(y):
                raise ValueError(f"{axis} step must be a nonzero Manhattan vector")
        cx, cy = self.column_step
        rx, ry = self.row_step
        if cx * rx + cy * ry:
            raise ValueError("column and row steps must be perpendicular")
        return self


class Instance(BaseModel):
    """A cell reference whose `transform` maps the first copy from child to parent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Name
    cell: Name
    transform: Transform = Transform()
    # Keep the published wire representation of ordinary instances unchanged.
    array: InstanceArray | None = Field(default=None, exclude_if=lambda array: array is None)

    def placement(self, column: int, row: int) -> tuple[str, Transform]:
        """Return the path and child-to-parent transform at zero-based indices."""
        if type(column) is not int or type(row) is not int:
            raise TypeError("instance placement indices must be integers")
        array = self.array
        if array is None:
            if (column, row) != (0, 0):
                raise IndexError("single instance placement indices must both be zero")
            return (self.name, self.transform)
        if not (0 <= column < array.columns and 0 <= row < array.rows):
            raise IndexError("instance array placement indices out of range")
        x, y = self.transform.translation
        cx, cy = array.column_step
        rx, ry = array.row_step
        translation = (x + column * cx + row * rx, y + column * cy + row * ry)
        return (
            f"{self.name}/{row}/{column}",
            self.transform.model_copy(update={"translation": translation}),
        )

    def placements(self) -> Iterator[tuple[str, Transform]]:
        """Yield placements in row-major order."""
        array = self.array
        if array is None:
            yield self.placement(0, 0)
            return
        for row in range(array.rows):
            for column in range(array.columns):
                yield self.placement(column, row)


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
