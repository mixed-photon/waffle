"""The gdsfactory layout backend: generation, GDS import and export, geometry operations.

All four capabilities of `waffle.layout.backend.interface` are implemented. Generation
builds components with gdsfactory on a private layout (`session`), converts them to Layout
IR, and returns the IR; no upstream object outlives a call.
"""

from __future__ import annotations

from importlib import metadata

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.unsupported import Unsupported, unsupported
from waffle.contracts.layout.layout import Layout
from waffle.layout.backend.interface import (
    BackendCode,
    BackendInfo,
    BooleanOperation,
    GenerationRequest,
    GeometryOperation,
    ImportRequest,
    diagnostic,
    layer_table,
    with_diagnostics,
)
from waffle.layout.formats.gds_stream import summarize
from waffle.layout.formats.sidecar import Producer, verify_sidecar
from waffle.layout.services.validate import validate_layout
from third_party.gdsfactory import geometry
from third_party.gdsfactory.convert import kfactory_ports, layout_from_klayout
from third_party.gdsfactory.gds import oversized_polygons, read_bytes, to_klayout, write_bytes
from third_party.gdsfactory.generators import (
    GENERATORS,
    LAYER,
    Canvas,
    lengths_in_dbu,
    technology_grid,
)
from third_party.gdsfactory.session import session

NAME = "waffle.gdsfactory"
ADAPTER_VERSION = "1.0.0"
UPSTREAM = ("gdsfactory", "kfactory", "klayout")


class GdsfactoryBackend:
    """Stateless; one instance may be used repeatedly (calls are serialized by `session`)."""

    def info(self) -> BackendInfo:
        return BackendInfo(
            producer=Producer(
                name=NAME,
                version=ADAPTER_VERSION,
                upstream={package: metadata.version(package) for package in UPSTREAM},
            ),
            generators=tuple(g.info for g in GENERATORS),
            import_formats=("gds",),
            export_formats=("gds",),
            geometry_operations=(GeometryOperation.MERGE, GeometryOperation.BOOLEAN),
        )

    def generate(self, request: GenerationRequest) -> Result[Layout]:
        source, technology = request.generator, request.technology
        generator = next(
            (
                g
                for g in GENERATORS
                if (g.info.name, g.info.version) == (source.name, source.version)
            ),
            None,
        )
        if generator is None:
            offered = ", ".join(f"{g.info.name} {g.info.version}" for g in GENERATORS)
            return Result(
                None,
                (
                    diagnostic(
                        BackendCode.GENERATOR_UNKNOWN,
                        f"no generator {source.name} {source.version}; offered: {offered}",
                    ),
                ),
            )
        subject = f"{source.name} {source.version}"
        problems = generator.info.parameters.check(request.values, subject)
        if problems:
            return Result(None, tuple(problems))
        wg = technology.layer(LAYER)
        if wg is None:
            return Result(
                None,
                (
                    diagnostic(
                        BackendCode.LAYER_MISSING,
                        f"technology {technology.id} has no layer {LAYER!r}",
                        subject=subject,
                    ),
                ),
            )
        grid = technology_grid(technology)
        if grid.value is None:
            return Result(None, grid.diagnostics)
        values = generator.info.parameters.with_defaults(request.values.values)
        lengths = lengths_in_dbu(values, grid.value, subject)
        if lengths.value is None:
            return Result(None, lengths.diagnostics)
        table = layer_table(technology)
        layout_id = request.layout_id or EntityId(
            "layout",
            f"{source.name.rsplit('.', 1)[-1]}_"
            + content_hash(
                {
                    "generator": [source.name, source.version],
                    "dbu": lengths.value,
                    "technology": str(revision_of(technology)),
                }
            )[7:19],
        )
        with session(technology.dbu_pm) as kcl:
            built = generator.build(Canvas(kcl, wg.gds_layer, wg.gds_datatype), lengths.value)
            if built.value is None:
                return Result(None, built.diagnostics)
            ports = kfactory_ports(kcl, table, subject=str(layout_id))
            if ports.value is None:
                return Result(None, ports.diagnostics)
            converted = layout_from_klayout(
                kcl.layout,
                layout_id=layout_id,
                grid=grid.value,
                layers=table,
                top=built.value.name,
                ports=ports.value,
                metadata={
                    "generator": source.name,
                    "generator_version": source.version,
                    "technology": str(revision_of(technology)),
                    "backend": NAME,
                    "backend_version": ADAPTER_VERSION,
                },
            )
        return with_diagnostics(converted, lengths.diagnostics)

    def export_gds(self, layout: Layout) -> Result[bytes]:
        problems = validate_layout(layout)
        if has_errors(problems):
            return Result(None, tuple(problems))
        ly = to_klayout(layout)
        if oversized := oversized_polygons(ly):
            return Result(None, tuple(oversized))
        return Result.of(write_bytes(ly))

    def import_gds(self, data: bytes, request: ImportRequest) -> Result[Layout]:
        sidecar = request.sidecar
        found: list[Diagnostic] = list(verify_sidecar(data, sidecar)) if sidecar else []
        stream = summarize(data)
        found.extend(stream.diagnostics)
        if stream.value is not None:
            found.extend(
                unsupported(
                    Unsupported.EXTERNAL_REFERENCE,
                    f"cell {name!r} is referenced but not defined in the file",
                    subject=str(request.result_id),
                )
                for name in stream.value.undefined
            )
        if has_errors(found) or stream.value is None:
            return Result(None, tuple(found))
        ly = read_bytes(data)
        if ly.value is None:
            return Result(None, ly.diagnostics)
        grid = sidecar.grid if sidecar else Grid(dbu_pm=stream.value.dbu_pm)
        converted = layout_from_klayout(
            ly.value,
            layout_id=request.result_id,
            grid=grid,
            layers=request.table,
            top=request.top or (sidecar.top if sidecar else None),
            ports={item.cell: item.ports for item in sidecar.ports} if sidecar else {},
        )
        if sidecar is None:
            found.append(
                diagnostic(
                    BackendCode.NO_PORTS,
                    "no sidecar was given: GDS cannot carry ports, so the layout has none",
                    severity=Severity.INFO,
                    subject=str(request.result_id),
                )
            )
        return with_diagnostics(converted, found)

    def merge(self, layout: Layout) -> Result[Layout]:
        return geometry.merge(layout)

    def boolean(self, layout: Layout, operation: BooleanOperation) -> Result[Layout]:
        return geometry.boolean(layout, operation)
