"""Polygon merge and Boolean operations with klayout regions.

Both work cell by cell on the shapes each cell holds itself (instances are untouched, so
hierarchy and ports survive) and go through the same conversion as every other import, so a
result the IR cannot hold (a polygon with a hole) is an unsupported-shape error. Results are
merged polygons: overlapping and edge-abutting input shapes become one polygon (corner
contact alone does not merge).
"""

from __future__ import annotations

import klayout.db as kdb

from waffle.contracts.foundation.diagnostics import has_errors
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.layout import Layout
from waffle.layout.backend.interface import BackendCode, BooleanOp, BooleanOperation, diagnostic
from waffle.layout.services.validate import validate_layout
from third_party.gdsfactory.convert import layout_from_klayout
from third_party.gdsfactory.gds import to_klayout


def _reimport(layout: Layout, ly: kdb.Layout) -> Result[Layout]:
    return layout_from_klayout(
        ly,
        layout_id=layout.id,
        grid=layout.grid,
        layers=layout.layers,
        top=layout.top,
        ports={cell.name: cell.ports for cell in layout.cells},
        metadata=layout.metadata,
    )


def merge(layout: Layout) -> Result[Layout]:
    problems = validate_layout(layout)
    if has_errors(problems):
        return Result(None, tuple(problems))
    ly = to_klayout(layout)
    for cell in ly.each_cell():
        for index in ly.layer_indexes():
            merged = kdb.Region(cell.shapes(index)).merged()
            cell.shapes(index).clear()
            cell.shapes(index).insert(merged)
    return _reimport(layout, ly)


def boolean(layout: Layout, operation: BooleanOperation) -> Result[Layout]:
    problems = validate_layout(layout)
    if has_errors(problems):
        return Result(None, tuple(problems))
    known = {spec.name: spec for spec in layout.layers}
    absent = [name for name in (operation.a, operation.b, operation.out) if name not in known]
    if absent:
        return Result(
            None,
            tuple(
                diagnostic(BackendCode.LAYER_MISSING, f"layer {name!r} is not in the layout")
                for name in absent
            ),
        )
    ly = to_klayout(layout)
    a, b, out = (
        ly.layer(known[name].layer, known[name].datatype)
        for name in (operation.a, operation.b, operation.out)
    )
    for cell in ly.each_cell():
        left, right = kdb.Region(cell.shapes(a)), kdb.Region(cell.shapes(b))
        result = {
            BooleanOp.AND: left & right,
            BooleanOp.OR: left | right,
            BooleanOp.NOT: left - right,
            BooleanOp.XOR: left ^ right,
        }[operation.op]
        cell.shapes(out).clear()
        cell.shapes(out).insert(result.merged())
    return _reimport(layout, ly)
