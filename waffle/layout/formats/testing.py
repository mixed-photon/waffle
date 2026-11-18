"""GDSII bytes for tests, written without any layout backend.

`gds_bytes` writes a valid stream with one BOUNDARY per shape, one SREF per instance (with
its mirror and rotation), and the database unit of the layout. It lets core tests exercise
sidecar checks on real stream structure; it is not a backend and does not read GDS.
"""

from __future__ import annotations

import math
import struct

from waffle.contracts.geometry.transform import Point
from waffle.contracts.layout.layout import Layout
from waffle.geometry.services.shapes import to_polygon


def _real8(value: float) -> bytes:
    if value == 0:
        return bytes(8)
    exponent = math.ceil(math.log(abs(value), 16))
    mantissa = round(abs(value) / 16.0**exponent * 2**56)
    if mantissa >= 2**56:
        exponent, mantissa = exponent + 1, mantissa // 16
    sign = 0x80 if value < 0 else 0
    return bytes([sign | (exponent + 64)]) + mantissa.to_bytes(7, "big")


def _record(kind: int, data_type: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HBB", 4 + len(payload), kind, data_type) + payload


def _name(text: str) -> bytes:
    raw = text.encode("ascii")
    return raw + b"\0" * (len(raw) % 2)


def _xy(points: list[Point]) -> bytes:
    flat = [v for p in points for v in p]
    return _record(0x10, 3, struct.pack(f">{len(flat)}i", *flat))


def gds_bytes(layout: Layout) -> bytes:
    """A GDSII stream of `layout`'s structure (instances of undefined cells are kept)."""
    pairs = {spec.name: (spec.layer, spec.datatype) for spec in layout.layers}
    dbu_um = layout.grid.dbu_pm / 1e6
    out = [
        _record(0x00, 2, struct.pack(">h", 600)),
        _record(0x01, 2, bytes(24)),
        _record(0x02, 6, _name("WAFFLE")),
        _record(0x03, 5, _real8(dbu_um) + _real8(layout.grid.dbu_pm * 1e-12)),
    ]
    for cell in layout.cells:
        out += [_record(0x05, 2, bytes(24)), _record(0x06, 6, _name(cell.name))]
        for layer, shapes in cell.shapes.items():
            number, datatype = pairs[layer]
            for shape in shapes:
                ring = list(to_polygon(shape).unwrap().points)
                out += [
                    _record(0x08, 0),
                    _record(0x0D, 2, struct.pack(">h", number)),
                    _record(0x0E, 2, struct.pack(">h", datatype)),
                    _xy([*ring, ring[0]]),
                    _record(0x11, 0),
                ]
        for inst in cell.instances:
            t = inst.transform
            out += [_record(0x0A, 0), _record(0x12, 6, _name(inst.cell))]
            if t.mirror or t.rotation:
                out += [_record(0x1A, 1, struct.pack(">H", 0x8000 if t.mirror else 0))]
                out += [_record(0x1C, 5, _real8(float(t.rotation)))]
            out += [_xy([t.translation]), _record(0x11, 0)]
        out.append(_record(0x07, 0))
    out.append(_record(0x04, 0))
    return b"".join(out)
