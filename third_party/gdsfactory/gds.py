"""Layout IR to klayout and back to GDS bytes, using klayout (the engine under gdsfactory).

Conventions, each checked against klayout 0.30.13:

* `kdb.Trans(rotation // 90, mirror, x, y)` is exactly `Transform`: mirror about the x axis
  first, then counter-clockwise rotation, then translation.
* Instance names are stored as the instance property with key 0, the same convention
  kfactory/gdsfactory use (`kfactory.conf.PROPID.NAME`), because GDS instances have no names.
* The GDS writer may reorient and reindex noncanonical arrays. Preflight constrains Waffle
  export to arrays whose axes and origin survive that writer unchanged.
* Shapes are written as polygons (paths as their outline); GDS timestamps are not written, so
  equal layouts give equal bytes.
* klayout's GDS writer silently splits polygons of more than 8000 vertices into several
  polygons; `oversized_polygons` finds them so export can refuse instead.
"""

from __future__ import annotations

import struct

import klayout.db as kdb
from pydantic import ValidationError

from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.transform import Point, Transform
from waffle.contracts.geometry.unsupported import Unsupported, unsupported
from waffle.contracts.layout.layout import InstanceArray, Layout
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.backend.interface import BackendCode, diagnostic

INSTANCE_NAME_KEY = 0
MAX_VERTICES = 8000
_MAX_ARRAY_COUNT = 32767
_MAX_COORDINATE = 2**31 - 1
_MIN_COORDINATE = -(2**31)
_ENDLIB, _STRNAME = 0x04, 0x06
_SREF, _AREF = 0x0A, 0x0B
_XY, _ENDEL, _COLROW = 0x10, 0x11, 0x13


def canonical_array(
    array: InstanceArray, transform: Transform
) -> tuple[InstanceArray, Transform]:
    """Index along positive child-local X (columns) and Y (rows)."""
    to_local = Transform(rotation=transform.rotation, mirror=transform.mirror).inverse()
    x, y = transform.translation
    columns, rows = 1, 1
    column_step: Point = (0, 0)
    row_step: Point = (0, 0)
    for count, (dx, dy) in (
        (array.columns, array.column_step),
        (array.rows, array.row_step),
    ):
        if count == 1:
            continue
        local_x, local_y = to_local.apply((dx, dy))
        if (local_x or local_y) < 0:
            x += (count - 1) * dx
            y += (count - 1) * dy
            dx, dy = -dx, -dy
        if local_x:
            columns, column_step = count, (dx, dy)
        else:
            rows, row_step = count, (dx, dy)
    return (
        InstanceArray(columns=columns, rows=rows, column_step=column_step, row_step=row_step),
        transform.model_copy(update={"translation": (x, y)}),
    )


def export_array_problems(layout: Layout) -> list[Diagnostic]:
    """Validate the array properties that the pinned GDS writer can preserve."""
    findings: list[Diagnostic] = []
    for cell in layout.cells:
        for instance in cell.instances:
            array, transform = instance.array, instance.transform
            if array is None:
                continue
            if max(array.columns, array.rows) > _MAX_ARRAY_COUNT:
                reason = f"GDS supports at most {_MAX_ARRAY_COUNT} columns and rows"
            elif (array, transform) != canonical_array(array, transform):
                reason = "GDS export requires columns along local +X and rows along local +Y"
            else:
                x, y = transform.translation
                cx, cy = array.column_step
                rx, ry = array.row_step
                # GDS stores one full count of each step, beyond the final member.
                column_dx, column_dy = cx * array.columns, cy * array.columns
                row_dx, row_dy = rx * array.rows, ry * array.rows
                deltas = (column_dx, column_dy, row_dx, row_dy)
                endpoints = (x, y, x + column_dx, y + column_dy, x + row_dx, y + row_dy)
                if all(
                    _MIN_COORDINATE <= coord <= _MAX_COORDINATE for coord in endpoints
                ) and all(abs(delta) <= _MAX_COORDINATE for delta in deltas):
                    continue
                reason = "GDS array coordinates and spans must fit signed 32-bit integers"
            findings.append(
                unsupported(
                    Unsupported.ARRAY_INSTANCE,
                    reason,
                    subject=str(layout.id),
                    location=("cells", cell.name, "instances", instance.name),
                )
            )
    return findings


def _wire_array_problem(counts: bytes, xy: bytes) -> str | None:
    if len(counts) != 4 or len(xy) != 24:
        return "GDS AREF must provide a COLROW record and three XY points"
    columns, rows = struct.unpack(">hh", counts)
    if min(columns, rows) < 1:
        return "GDS AREF column and row counts must be positive"
    if columns == rows == 1:
        return None
    x, y, column_x, column_y, row_x, row_y = struct.unpack(">6i", xy)
    steps: list[Point] = []
    for count, dx, dy in ((columns, column_x - x, column_y - y), (rows, row_x - x, row_y - y)):
        if count == 1:
            steps.append((0, 0))
            continue
        if max(abs(dx), abs(dy)) > _MAX_COORDINATE:
            return "GDS array spans must fit signed 32-bit integers"
        if dx % count or dy % count:
            return "GDS AREF spacing must be an integer number of database units"
        steps.append((dx // count, dy // count))
    try:
        InstanceArray(columns=columns, rows=rows, column_step=steps[0], row_step=steps[1])
    except ValidationError as exc:
        return exc.errors()[0]["msg"]
    return None


def import_array_problems(data: bytes, *, subject: str) -> list[Diagnostic]:
    """Check AREF records before klayout rounds, splits, or drops invalid arrays.

    The caller has already checked the GDS record framing with `summarize`.
    """
    findings: list[Diagnostic] = []
    offset, index = 0, -1
    cell = ""
    aref = False
    counts = xy = b""
    while offset < len(data):
        length, kind = struct.unpack_from(">HB", data, offset)
        start = offset + 4
        offset += length
        if kind == _ENDLIB:
            break
        if kind == _STRNAME:
            cell = data[start:offset].rstrip(b"\0").decode("ascii", "replace")
            index = -1
        elif kind in (_AREF, _SREF):
            aref = kind == _AREF
            index += 1
            counts = xy = b""
        elif aref and kind == _COLROW:
            counts = data[start:offset]
        elif aref and kind == _XY:
            xy = data[start:offset]
        elif aref and kind == _ENDEL:
            aref = False
            if reason := _wire_array_problem(counts, xy):
                findings.append(
                    unsupported(
                        Unsupported.ARRAY_INSTANCE,
                        reason,
                        subject=subject,
                        location=("cells", cell, "instances", index),
                    )
                )
    return findings


def to_klayout(layout: Layout) -> kdb.Layout:
    """A klayout layout with the cells, shapes, and named instances of a valid `layout`."""
    ly = kdb.Layout()
    ly.dbu = layout.grid.dbu_pm / 1e6
    indexes = {spec.name: ly.layer(spec.layer, spec.datatype) for spec in layout.layers}
    cells = {cell.name: ly.create_cell(cell.name) for cell in layout.cells}
    for cell in layout.cells:
        target = cells[cell.name]
        for layer, shapes in cell.shapes.items():
            for shape in shapes:
                ring = to_polygon(shape).unwrap().points
                target.shapes(indexes[layer]).insert(
                    kdb.Polygon([kdb.Point(x, y) for x, y in ring])
                )
        for instance in cell.instances:
            t = instance.transform
            child = cells[instance.cell].cell_index()
            transform = kdb.Trans(t.rotation // 90, t.mirror, *t.translation)
            array = instance.array
            if array is None:
                ref = kdb.CellInstArray(child, transform)
            else:
                ref = kdb.CellInstArray(
                    child,
                    transform,
                    kdb.Vector(*array.column_step),
                    kdb.Vector(*array.row_step),
                    array.columns,
                    array.rows,
                )
            placed = target.insert(ref)
            placed.set_property(INSTANCE_NAME_KEY, instance.name)
    return ly


def oversized_polygons(ly: kdb.Layout) -> list[Diagnostic]:
    """An error for every cell holding a polygon klayout would split when writing GDS."""
    counts: dict[str, int] = {}
    for cell in ly.each_cell():
        for index in ly.layer_indexes():
            for shape in cell.shapes(index).each():
                counts[cell.name] = max(counts.get(cell.name, 0), shape.polygon.num_points())
    return [
        diagnostic(
            BackendCode.EXPORT_FAILED,
            f"a polygon has {count} vertices; GDS export would split anything over {MAX_VERTICES}",
            location=("cells", name),
        )
        for name, count in sorted(counts.items())
        if count > MAX_VERTICES
    ]


def write_bytes(ly: kdb.Layout) -> bytes:
    options = kdb.SaveLayoutOptions()
    options.format = "GDS2"
    options.gds2_write_timestamps = False
    return bytes(ly.write_bytes(options))


def read_bytes(data: bytes) -> Result[kdb.Layout]:
    ly = kdb.Layout()
    try:
        ly.read_bytes(data)
    except RuntimeError as exc:
        return Result(
            None, (diagnostic(BackendCode.IMPORT_FAILED, f"klayout cannot read the GDS: {exc}"),)
        )
    return Result.of(ly)
