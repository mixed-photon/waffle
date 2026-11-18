"""A read-only structural summary of a GDSII stream, independent of any layout backend.

Only record headers are read: the database unit, cell names, which cells are referenced,
and the `(layer, datatype)` pairs in use. That is enough to check a GDS file against its
sidecar (`waffle.layout.formats.sidecar`) without importing any upstream library, and to
spot references to cells the file does not define. Geometry is not decoded.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result

# Record types (the byte after the length), see the GDSII stream format specification.
_UNITS, _ENDLIB = 0x03, 0x04
_STRNAME, _SNAME = 0x06, 0x12
_LAYER = 0x0D
_PAIR_TYPES = {0x0E, 0x16, 0x2E}  # DATATYPE, TEXTTYPE, BOXTYPE: completes a (layer, type) pair
_PM_PER_METRE = 10**12


@dataclass(frozen=True)
class GdsSummary:
    """What the stream declares. `top_cells` are defined cells no other cell references and
    `undefined` are referenced names with no definition."""

    dbu_pm: int
    cells: tuple[str, ...]
    referenced: frozenset[str]
    layers: frozenset[tuple[int, int]]

    @property
    def top_cells(self) -> tuple[str, ...]:
        return tuple(name for name in self.cells if name not in self.referenced)

    @property
    def undefined(self) -> tuple[str, ...]:
        return tuple(sorted(self.referenced - set(self.cells)))


def _invalid(message: str) -> Result[GdsSummary]:
    return Result(
        None,
        (Diagnostic(severity=Severity.ERROR, code="gds.stream.invalid", message=message),),
    )


def _real8(data: bytes) -> float:
    """A GDSII 8-byte real: sign bit, excess-64 base-16 exponent, 56-bit mantissa."""
    sign = -1.0 if data[0] & 0x80 else 1.0
    mantissa = int.from_bytes(data[1:8], "big") / 2**56
    return sign * mantissa * 16.0 ** ((data[0] & 0x7F) - 64)


def summarize(data: bytes) -> Result[GdsSummary]:
    """Summarize a GDSII stream, or report why it is not one (`gds.stream.invalid`)."""
    cells: list[str] = []
    referenced: set[str] = set()
    layers: set[tuple[int, int]] = set()
    dbu_pm: int | None = None
    layer = 0
    offset = 0
    ended = False
    while offset < len(data) and not ended:
        if offset + 4 > len(data):
            return _invalid(f"truncated record header at byte {offset}")
        length, kind = struct.unpack_from(">HB", data, offset)
        if length < 4 or length % 2 or offset + length > len(data):
            return _invalid(f"bad record length {length} at byte {offset}")
        body = data[offset + 4 : offset + length]
        offset += length
        if kind == _UNITS:
            if len(body) != 16:
                return _invalid("UNITS record must hold two 8-byte reals")
            metres = _real8(body[8:])
            dbu_pm = round(metres * _PM_PER_METRE)
            if dbu_pm <= 0 or abs(metres * _PM_PER_METRE - dbu_pm) > 1e-6:
                return _invalid(f"database unit {metres} m is not a whole number of picometres")
        elif kind == _STRNAME:
            cells.append(body.rstrip(b"\0").decode("ascii", "replace"))
        elif kind == _SNAME:
            referenced.add(body.rstrip(b"\0").decode("ascii", "replace"))
        elif kind == _LAYER and len(body) == 2:
            layer = struct.unpack(">h", body)[0]
        elif kind in _PAIR_TYPES and len(body) == 2:
            layers.add((layer, struct.unpack(">h", body)[0]))
        elif kind == _ENDLIB:
            ended = True
    if not ended:
        return _invalid("stream has no ENDLIB record")
    if dbu_pm is None:
        return _invalid("stream has no UNITS record")
    return Result.of(GdsSummary(dbu_pm, tuple(cells), frozenset(referenced), frozenset(layers)))
