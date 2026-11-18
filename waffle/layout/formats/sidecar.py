"""The GDS sidecar: what a GDS file cannot say about itself.

A GDS file has no Waffle ports, no logical layer names, and no link to the layout revision it
came from. The sidecar record `<name>.gds.waffle.json` sits next to `<name>.gds` and holds
all of that, plus the sha256 and size of the exact GDS bytes it describes, so a modified or
mismatched file is detected. It is backend neutral: any backend builds one for its export
with `build_sidecar`, and `verify_sidecar` needs only `waffle.layout.formats.gds_stream`.

Ports of every cell are stored (not only the top cell's) so a re-imported layout can be
compared hierarchically with the original. Diagnostic codes: `gds.file.missing`,
`gds.sidecar.missing`, `gds.sidecar.size_mismatch`, `gds.sidecar.hash_mismatch`,
`gds.sidecar.top_mismatch`, `gds.sidecar.grid_mismatch`, `gds.sidecar.layer_mismatch`,
`gds.sidecar.cell_mismatch`, and `gds.stream.invalid`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from waffle.contracts.component.refs import DesignRef, ImplementationRef, TechnologyRef
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import (
    Record,
    RevisionRefField,
    load_record,
    ref_to,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.layout.layout import LayerSpec, Layout, Name, Port
from waffle.layout.formats.gds_stream import summarize

SIDECAR_SUFFIX = ".waffle.json"
_SHA256 = r"^sha256:[0-9a-f]{64}$"

LayoutRef = Annotated[RevisionRefField, ref_to("layout")]


class Producer(BaseModel):
    """The backend that wrote the GDS: its name and version, and the exact versions of the
    upstream libraries it ran on (`{"gdsfactory": "9.51.0", ...}`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    upstream: dict[str, str] = Field(default_factory=dict)


class CellPorts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: Name
    ports: tuple[Port, ...]


class GdsSidecar(Record):
    """Association of one GDS file (by digest) with the layout it was exported from."""

    kind: Literal["gds_sidecar"] = "gds_sidecar"
    schema_version: Literal[1] = 1
    sha256: str = Field(pattern=_SHA256)
    size: int = Field(ge=0)
    layout: LayoutRef
    grid: Grid
    top: Name
    layers: tuple[LayerSpec, ...]
    ports: tuple[CellPorts, ...] = ()
    producer: Producer
    design: DesignRef | None = None
    technology: TechnologyRef | None = None
    implementation: ImplementationRef | None = None

    @field_validator("ports")
    @classmethod
    def _sort_ports(cls, ports: tuple[CellPorts, ...]) -> tuple[CellPorts, ...]:
        return tuple(sorted(ports, key=lambda item: item.cell))

    def ports_of(self, cell: str) -> tuple[Port, ...]:
        return next((item.ports for item in self.ports if item.cell == cell), ())


@dataclass(frozen=True)
class GdsPackage:
    """GDS bytes together with the sidecar describing them."""

    gds: bytes
    sidecar: GdsSidecar


def digest_of(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def build_sidecar(
    layout: Layout,
    gds: bytes,
    producer: Producer,
    *,
    design: DesignRef | None = None,
    technology: TechnologyRef | None = None,
    implementation: ImplementationRef | None = None,
    metadata: dict[str, str] | None = None,
) -> GdsSidecar:
    """The sidecar of `gds`, which must have been exported from the (valid) `layout`."""
    return GdsSidecar(
        id=EntityId("gds_sidecar", layout.id.name),
        sha256=digest_of(gds),
        size=len(gds),
        layout=revision_of(layout),
        grid=layout.grid,
        top=layout.top,
        layers=layout.layers,
        ports=tuple(
            CellPorts(cell=cell.name, ports=cell.ports) for cell in layout.cells if cell.ports
        ),
        producer=producer,
        design=design,
        technology=technology,
        implementation=implementation,
        metadata=metadata or {},
    )


def verify_sidecar(gds: bytes, sidecar: GdsSidecar) -> list[Diagnostic]:
    """Everything that says `gds` is not the file `sidecar` describes; empty when it is.

    Integrity first (size and digest: a modified file or the wrong sidecar), and only for
    matching bytes the structure: the sidecar's top cell is a top cell of the stream, the
    database units agree, every `(layer, datatype)` in use is in the sidecar's layer table,
    and every cell with ports exists.
    """
    subject = str(sidecar.id)

    def error(code: str, message: str) -> Diagnostic:
        return Diagnostic(
            severity=Severity.ERROR, code=f"gds.sidecar.{code}", message=message, subject=subject
        )

    if len(gds) != sidecar.size:
        return [error("size_mismatch", f"file has {len(gds)} bytes, sidecar says {sidecar.size}")]
    if (digest := digest_of(gds)) != sidecar.sha256:
        return [error("hash_mismatch", f"file digest {digest} differs from {sidecar.sha256}")]
    summary = summarize(gds)
    if summary.value is None:
        return [d.model_copy(update={"subject": subject}) for d in summary.diagnostics]
    stream = summary.value
    found: list[Diagnostic] = []
    if sidecar.top not in stream.top_cells:
        found.append(
            error(
                "top_mismatch",
                f"sidecar top cell {sidecar.top!r} is not a top cell of the file "
                f"({', '.join(map(repr, stream.top_cells)) or 'none'})",
            )
        )
    if stream.dbu_pm != sidecar.grid.dbu_pm:
        found.append(
            error(
                "grid_mismatch",
                f"file database unit is {stream.dbu_pm} pm, sidecar says {sidecar.grid.dbu_pm} pm",
            )
        )
    known = {(spec.layer, spec.datatype) for spec in sidecar.layers}
    if unknown := sorted(stream.layers - known):
        found.append(
            error("layer_mismatch", f"layers {unknown} are not in the sidecar layer table")
        )
    if absent := sorted({p.cell for p in sidecar.ports} - set(stream.cells)):
        found.append(
            error("cell_mismatch", f"sidecar ports belong to cells not in the file: {absent}")
        )
    return found


def sidecar_path(gds_path: Path) -> Path:
    """`<name>.gds` is described by `<name>.gds.waffle.json`."""
    return gds_path.with_name(gds_path.name + SIDECAR_SUFFIX)


def write_package(gds_path: Path, package: GdsPackage) -> Path:
    """Write the GDS and its sidecar side by side; returns the sidecar path."""
    gds_path.write_bytes(package.gds)
    path = sidecar_path(gds_path)
    path.write_text(to_canonical_json(package.sidecar), encoding="utf-8")
    return path


def read_package(gds_path: Path) -> Result[GdsPackage]:
    """Read `<name>.gds` and its sidecar and verify them against each other."""
    path = sidecar_path(gds_path)
    for missing, code, what in (
        (gds_path, "gds.file.missing", "GDS file"),
        (path, "gds.sidecar.missing", "sidecar"),
    ):
        if not missing.is_file():
            return Result(
                None,
                (
                    Diagnostic(
                        severity=Severity.ERROR,
                        code=code,
                        message=f"{what} {missing} does not exist",
                    ),
                ),
            )
    loaded = load_record(path.read_text(encoding="utf-8"), GdsSidecar)
    if loaded.value is None:
        return Result(None, loaded.diagnostics)
    gds = gds_path.read_bytes()
    found = verify_sidecar(gds, loaded.value)
    return (
        Result.of(GdsPackage(gds, loaded.value), found)
        if not has_errors(found)
        else Result(None, tuple(found))
    )
