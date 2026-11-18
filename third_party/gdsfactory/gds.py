"""Layout IR to klayout and back to GDS bytes, using klayout (the engine under gdsfactory).

Conventions, each checked against klayout 0.30.13:

* `kdb.Trans(rotation // 90, mirror, x, y)` is exactly `Transform`: mirror about the x axis
  first, then counter-clockwise rotation, then translation.
* Instance names are stored as the instance property with key 0, the same convention
  kfactory/gdsfactory use (`kfactory.conf.PROPID.NAME`), because GDS instances have no names.
* Shapes are written as polygons (paths as their outline); GDS timestamps are not written, so
  equal layouts give equal bytes.
* klayout's GDS writer silently splits polygons of more than 8000 vertices into several
  polygons; `oversized_polygons` finds them so export can refuse instead.
"""

from __future__ import annotations

import klayout.db as kdb

from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.layout import Layout
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.backend.interface import BackendCode, diagnostic

INSTANCE_NAME_KEY = 0
MAX_VERTICES = 8000


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
            placed = target.insert(
                kdb.CellInstArray(
                    cells[instance.cell].cell_index(),
                    kdb.Trans(t.rotation // 90, t.mirror, *t.translation),
                )
            )
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
