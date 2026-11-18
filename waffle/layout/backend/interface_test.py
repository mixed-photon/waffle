from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from waffle.contracts.component.definition import (
    GeneratorSource,
    LayoutArtifactSource,
    PhysicalImplementation,
)
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.component.examples import PhaseShifterExample, phase_shifter
from waffle.contracts.foundation.diagnostics import Severity
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Layout
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.layout.backend.fake import NAME, VERSION, FakeBackend
from waffle.layout.backend.interface import (
    BackendCode,
    BackendInfo,
    Capability,
    GeometryOperation,
    ImportRequest,
    LayoutBackend,
    diagnostic,
    exporter_of,
    generator_of,
    geometry_of,
    importer_of,
    layer_table,
    request_for_design,
    with_diagnostics,
)
from waffle.layout.backend.packaging import export_package
from waffle.layout.formats.sidecar import Producer, build_sidecar
from waffle.pdk.testing.synthetic import synthetic_soi

PRODUCER = Producer(name="partial", version="0.1")


class ExportOnly:
    def info(self) -> BackendInfo:
        return BackendInfo(producer=PRODUCER, export_formats=("gds",))

    def export_gds(self, layout: Layout) -> Result[bytes]:
        return Result.of(b"")


class Overpromising:
    """Declares generation but does not implement it."""

    def info(self) -> BackendInfo:
        return BackendInfo(producer=PRODUCER, generators=FakeBackend().info().generators)


def test_capabilities_follow_what_is_listed() -> None:
    assert BackendInfo(producer=PRODUCER).capabilities == frozenset()
    assert ExportOnly().info().capabilities == {Capability.EXPORT}
    info = FakeBackend().info()
    assert info.capabilities == {Capability.GENERATION, Capability.IMPORT, Capability.EXPORT}
    geometry = BackendInfo(producer=PRODUCER, geometry_operations=(GeometryOperation.MERGE,))
    assert geometry.capabilities == {Capability.GEOMETRY}
    assert info.generator(NAME, VERSION) is info.generators[0]
    assert info.generator(NAME, "0") is None


def test_declared_capabilities_resolve_to_the_backend() -> None:
    backend = FakeBackend()
    assert generator_of(backend).value is backend
    assert importer_of(backend).value is backend
    assert exporter_of(backend).value is backend
    exporter = ExportOnly()
    assert exporter_of(exporter).value is exporter


@pytest.mark.parametrize(
    ("backend", "lookup", "named"),
    [
        (FakeBackend(), geometry_of, "geometry"),
        (ExportOnly(), generator_of, "generation"),
        (ExportOnly(), importer_of, "import"),
        (Overpromising(), generator_of, "generation"),
    ],
)
def test_missing_capability_is_a_structured_diagnostic(
    backend: LayoutBackend, lookup: Callable[[LayoutBackend], Result[Any]], named: str
) -> None:
    result = lookup(backend)
    assert result.value is None
    (found,) = result.diagnostics
    assert (
        found.code == BackendCode.CAPABILITY_UNSUPPORTED == "layout.backend.capability_unsupported"
    )
    assert named in found.message and found.subject == backend.info().producer.name


def test_layer_table_follows_the_technology() -> None:
    table = {(s.name, s.layer, s.datatype) for s in layer_table(synthetic_soi())}
    assert table == {("WG", 1, 0), ("SLAB", 2, 0), ("PORT", 1, 10), ("DEVREC", 68, 0)}


def test_import_request_must_be_self_sufficient() -> None:
    layout = nested_layout()
    sidecar = build_sidecar(layout, b"x", PRODUCER)
    assert ImportRequest(sidecar=sidecar).result_id == EntityId("layout", "nested")
    assert ImportRequest(sidecar=sidecar).table == layout.layers
    bare = ImportRequest(layers=layout.layers, layout_id=EntityId("layout", "bare"))
    assert bare.result_id == EntityId("layout", "bare") and bare.table == layout.layers
    for incomplete in ({}, {"layers": layout.layers}, {"layout_id": EntityId("layout", "x")}):
        with pytest.raises(ValidationError):
            ImportRequest(**incomplete)
    with pytest.raises(ValidationError):
        ImportRequest(layers=layout.layers, layout_id=EntityId("design", "x"))


def pinned_design() -> tuple[PhaseShifterExample, ResolvedDesign, TechnologyRevision]:
    """The phase-shifter chain with its design pinned to the synthetic technology."""
    example = phase_shifter()
    technology = synthetic_soi()
    design = example.design.model_copy(update={"technology": revision_of(technology)})
    return example, design, technology


def test_request_for_design_uses_the_pinned_revisions() -> None:
    example, design, technology = pinned_design()
    request = request_for_design(design, example.implementation, technology).unwrap()
    assert request.generator == GeneratorSource(name="phase_shifter_gen", version="1.2.0")
    assert request.values == design.parameters
    assert request.technology == technology
    assert request.layout_id == EntityId("layout", "phase_shifter_100um")


def test_request_for_design_rejects_unpinned_inputs() -> None:
    example, design, technology = pinned_design()
    other_tech = technology.model_copy(update={"metadata": {"changed": "yes"}})
    result = request_for_design(design, example.implementation, other_tech)
    assert [d.code for d in result.diagnostics] == [BackendCode.REQUEST_INVALID]
    assert "technology" in result.diagnostics[0].message
    other_impl = example.implementation.model_copy(update={"metadata": {"changed": "yes"}})
    result = request_for_design(design, other_impl, technology)
    assert "implementation" in result.diagnostics[0].message
    artifact = PhysicalImplementation(
        id=example.implementation.id,
        component=example.implementation.component,
        source=LayoutArtifactSource(artifact=ArtifactRef("layout_gds", "sha256:" + "a" * 64)),
        technology=example.implementation.technology,
    )
    pinned = design.model_copy(update={"implementation": revision_of(artifact)})
    result = request_for_design(pinned, artifact, technology)
    assert "not produced by a generator" in result.diagnostics[0].message


def test_export_package_ties_bytes_layout_and_refs_together() -> None:
    example, design, technology = pinned_design()
    backend = FakeBackend()
    layout = nested_layout()
    package = export_package(
        backend,
        layout,
        design=revision_of(design),
        technology=revision_of(technology),
        implementation=revision_of(example.implementation),
        metadata={"purpose": "test"},
    ).unwrap()
    sidecar = package.sidecar
    assert sidecar.producer == backend.info().producer
    assert (sidecar.layout, sidecar.design) == (revision_of(layout), revision_of(design))
    assert sidecar.technology == revision_of(technology)
    assert sidecar.metadata == {"purpose": "test"}


def test_export_package_passes_exporter_errors_through() -> None:
    broken = nested_layout().model_copy(update={"top": "missing"})
    result = export_package(FakeBackend(), broken)
    assert result.value is None
    assert [d.code for d in result.diagnostics] == ["layout.top.unknown"]


def test_earlier_findings_come_first_and_an_error_voids_the_value() -> None:
    note = diagnostic("layout.backend.note", "fyi", severity=Severity.INFO)
    error = diagnostic(BackendCode.REQUEST_INVALID, "bad")
    kept = with_diagnostics(Result(1), [note])
    assert (kept.value, kept.diagnostics) == (1, (note,))
    voided = with_diagnostics(Result(1), [error])
    assert (voided.value, voided.diagnostics) == (None, (error,))
    failed = with_diagnostics(Result(None, (error,)), [note])
    assert (failed.value, failed.diagnostics) == (None, (note, error))
