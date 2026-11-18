"""Export a layout with its sidecar through any `LayoutExporter`."""

from __future__ import annotations

from waffle.contracts.component.refs import DesignRef, ImplementationRef, TechnologyRef
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.layout import Layout
from waffle.layout.backend.interface import LayoutExporter
from waffle.layout.formats.sidecar import GdsPackage, build_sidecar, verify_sidecar


def export_package(
    exporter: LayoutExporter,
    layout: Layout,
    *,
    design: DesignRef | None = None,
    technology: TechnologyRef | None = None,
    implementation: ImplementationRef | None = None,
    metadata: dict[str, str] | None = None,
) -> Result[GdsPackage]:
    """The GDS bytes of `layout` and the sidecar tying them to the layout revision, the
    optional design, technology, and implementation revisions, and the exporting backend.
    The sidecar is checked against the bytes before it is returned."""
    exported = exporter.export_gds(layout)
    if exported.value is None:
        return Result(None, exported.diagnostics)
    sidecar = build_sidecar(
        layout,
        exported.value,
        exporter.info().producer,
        design=design,
        technology=technology,
        implementation=implementation,
        metadata=metadata,
    )
    return Result.of(
        GdsPackage(exported.value, sidecar),
        [*exported.diagnostics, *verify_sidecar(exported.value, sidecar)],
    )
