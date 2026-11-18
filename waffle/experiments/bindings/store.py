"""Publish and replay immutable bindings with exact record snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.component.definition import (
    ComponentDefinition,
    InterfaceDefinition,
    LayoutArtifactSource,
    PhysicalImplementation,
)
from waffle.contracts.component.design import Circuit, ResolvedDesign, Specimen
from waffle.contracts.experiments.binding import ExperimentBinding
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import RevisionRef
from waffle.contracts.foundation.records import Record, load_record, revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.layout import Layout
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.models.model import ArtifactPayload, ModelRevision
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.dataset import MeasurementDataset
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.design.mappings.validate import validate_mapping
from waffle.models.applicability import check_applicability

_KINDS: dict[str, type[Record]] = {
    "circuit": Circuit,
    "design": ResolvedDesign,
    "implementation": PhysicalImplementation,
    "component": ComponentDefinition,
    "interface": InterfaceDefinition,
    "model": ModelRevision,
    "view_mapping": ViewMapping,
    "layout": Layout,
    "physical_problem": PhysicalProblem,
    "technology": TechnologyRevision,
    "specimen": Specimen,
    "measurement_dataset": MeasurementDataset,
}


@dataclass(frozen=True)
class ReplayedBinding:
    binding: ExperimentBinding
    records: Mapping[RevisionRef, Record]


def _error(code: str, message: str, subject: object) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=str(subject)
    )


def _required(
    binding: ExperimentBinding, records: Mapping[RevisionRef, Record]
) -> set[RevisionRef]:
    refs = {binding.circuit}
    for entry in binding.instances:
        refs.update(
            (
                entry.design,
                entry.implementation,
                entry.component,
                entry.interface,
                entry.model,
                entry.mapping,
            )
        )
    pending = list(refs)
    while pending:
        record = records.get(pending.pop())
        dependencies: set[RevisionRef] = set()
        if isinstance(record, Circuit):
            dependencies.update(instance.design for instance in record.instances)
        elif isinstance(record, ResolvedDesign):
            dependencies.update((record.implementation, record.technology))
        elif isinstance(record, PhysicalImplementation):
            dependencies.add(record.component)
        elif isinstance(record, ComponentDefinition):
            dependencies.add(record.interface)
        elif isinstance(record, ViewMapping):
            dependencies.update((record.interface, record.layout, record.problem))
            if record.model is not None:
                dependencies.add(record.model)
        elif isinstance(record, PhysicalProblem):
            dependencies.update((record.layout, record.technology))
            if record.design is not None:
                dependencies.add(record.design)
        elif isinstance(record, ModelRevision):
            dependencies.add(record.interface)
            dependencies.update(
                evidence.ref
                for evidence in record.evidence
                if isinstance(evidence.ref, RevisionRef)
            )
        elif isinstance(record, Specimen):
            dependencies.add(record.design)
        elif isinstance(record, MeasurementDataset):
            dependencies.update((record.specimen, record.design, record.mapping))
            if record.parent is not None:
                dependencies.add(record.parent)
        pending.extend(dependencies - refs)
        refs.update(dependencies)
    return refs


def _validate_snapshots(
    binding: ExperimentBinding, records: Mapping[RevisionRef, Record]
) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for ref in sorted(_required(binding, records), key=str):
        if ref.entity.kind not in _KINDS:
            found.append(
                _error(
                    "binding.unsupported_record_kind",
                    f"historical dependency {ref} has no snapshot reader",
                    binding.id,
                )
            )
            continue
        record = records.get(ref)
        if record is None:
            found.append(
                _error("binding.record_missing", f"record {ref} is missing", binding.id)
            )
        elif (
            type(record) is not _KINDS.get(ref.entity.kind)
            or revision_of(record) != ref
        ):
            found.append(
                _error(
                    "binding.record_mismatch",
                    f"record {ref} has the wrong type or digest",
                    binding.id,
                )
            )
    if found:
        return found
    circuit = records[binding.circuit]
    assert isinstance(circuit, Circuit)
    by_name = {instance.name: instance for instance in circuit.instances}
    if len(by_name) != len(circuit.instances):
        found.append(
            _error(
                "binding.duplicate_instance",
                "circuit contains duplicate instance names",
                binding.id,
            )
        )
    if set(by_name) != {entry.instance for entry in binding.instances}:
        found.append(
            _error(
                "binding.instance_mismatch",
                "binding does not cover every circuit instance exactly once",
                binding.id,
            )
        )
    for entry in binding.instances:
        instance = by_name.get(entry.instance)
        if instance is None or instance.design != entry.design:
            found.append(
                _error(
                    "binding.design_mismatch",
                    f"instance {entry.instance!r} has a different design",
                    binding.id,
                )
            )
            continue
        design = records[entry.design]
        implementation = records[entry.implementation]
        component = records[entry.component]
        model = records[entry.model]
        mapping = records[entry.mapping]
        assert isinstance(design, ResolvedDesign)
        assert isinstance(implementation, PhysicalImplementation)
        assert isinstance(component, ComponentDefinition)
        assert isinstance(model, ModelRevision)
        assert isinstance(mapping, ViewMapping)
        if (
            design.implementation != entry.implementation
            or implementation.component != entry.component
            or component.interface != entry.interface
            or model.interface != entry.interface
        ):
            found.append(
                _error(
                    "binding.dependency_mismatch",
                    f"instance {entry.instance!r} dependency chain differs",
                    binding.id,
                )
            )
        if mapping.model != entry.model or mapping.interface != entry.interface:
            found.append(
                _error(
                    "binding.mapping_mismatch",
                    f"instance {entry.instance!r} mapping pins differ",
                    binding.id,
                )
            )
        problem = records[mapping.problem]
        layout = records[mapping.layout]
        interface = records[entry.interface]
        assert isinstance(problem, PhysicalProblem)
        assert isinstance(layout, Layout)
        assert isinstance(interface, InterfaceDefinition)
        if (
            problem.layout != mapping.layout
            or problem.technology != design.technology
            or (problem.design is not None and problem.design != entry.design)
        ):
            found.append(
                _error(
                    "binding.problem_mismatch",
                    f"instance {entry.instance!r} physical problem pins differ",
                    binding.id,
                )
            )
        found.extend(validate_mapping(mapping, interface, layout, problem, model))
        found.extend(
            check_applicability(
                model,
                interface,
                entry.analysis,
                entry.operating,
                design=design,
                implementation=entry.implementation,
                mapping=mapping,
                requested_channels=entry.requested_channels,
            )
        )
        payload = model.payload
        if isinstance(payload, ArtifactPayload):
            if (
                entry.payload_artifact != payload.artifact
                or entry.evaluator_name is not None
            ):
                found.append(
                    _error(
                        "binding.payload_mismatch",
                        f"instance {entry.instance!r} payload pin differs",
                        binding.id,
                    )
                )
        elif (
            entry.evaluator_name != payload.name
            or entry.evaluator_version != payload.version
            or entry.payload_artifact is not None
        ):
            found.append(
                _error(
                    "binding.payload_mismatch",
                    f"instance {entry.instance!r} evaluator pin differs",
                    binding.id,
                )
            )
    return found


def _artifacts(
    binding: ExperimentBinding, records: Mapping[RevisionRef, Record]
) -> set[ArtifactRef]:
    refs = {
        entry.payload_artifact
        for entry in binding.instances
        if entry.payload_artifact is not None
    }
    for ref in _required(binding, records):
        record = records[ref]
        if isinstance(record, ModelRevision):
            refs.update(
                evidence.ref
                for evidence in record.evidence
                if isinstance(evidence.ref, ArtifactRef)
            )
            if isinstance(record.payload, ArtifactPayload):
                refs.add(record.payload.artifact)
        elif isinstance(record, PhysicalImplementation) and isinstance(
            record.source, LayoutArtifactSource
        ):
            refs.add(record.source.artifact)
        elif isinstance(record, MeasurementDataset):
            refs.update((record.raw_artifact, record.data_artifact))
    return refs


def _verify_artifacts(
    store: ArtifactStore, refs: Iterable[ArtifactRef]
) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for ref in sorted(refs):
        found.extend(store.verify(ref))
    return found


def publish_binding(
    store: ArtifactStore, binding: ExperimentBinding, snapshots: Iterable[Record]
) -> Result[ArtifactRef]:
    """Atomically publish a binding and all required record snapshots."""
    records = {revision_of(record): record for record in snapshots}
    findings = _validate_snapshots(binding, records)
    if has_errors(findings):
        return Result(None, tuple(findings))
    artifacts = _artifacts(binding, records)
    findings.extend(_verify_artifacts(store, artifacts))
    if has_errors(findings):
        return Result(None, tuple(findings))
    operation = OperationInfo(
        name="experiment.binding.publish",
        version="1",
        parameters_digest=content_hash(binding.model_dump(mode="json")),
    )
    inputs = [
        InputLink(role="record", target=ref)
        for ref in sorted(_required(binding, records), key=str)
    ]
    inputs.extend(InputLink(role="artifact", target=ref) for ref in sorted(artifacts))
    try:
        with store.attempt(operation, inputs) as session:
            writer = session.new_artifact("experiment_binding")
            writer.write_json("binding.json", binding.model_dump(mode="json"))
            writer.write_json(
                "records.json",
                {
                    str(ref): records[ref].model_dump(mode="json")
                    for ref in sorted(_required(binding, records), key=str)
                },
            )
            artifact = writer.commit()
    except ArtifactError as exc:
        return Result(None, tuple(exc.diagnostics))
    return Result.of(artifact, findings)


def replay_binding(
    store: ArtifactStore, artifact: ArtifactRef
) -> Result[ReplayedBinding]:
    """Load recorded pins only; never query a current catalog or reselect models."""
    if artifact.kind != "experiment_binding":
        return Result(
            None,
            (
                _error(
                    "binding.artifact_kind",
                    "expected an experiment_binding artifact",
                    artifact,
                ),
            ),
        )
    try:
        binding_data = json.loads(store.read_bytes(artifact, "binding.json"))
        record_data = json.loads(store.read_bytes(artifact, "records.json"))
    except ArtifactError as exc:
        return Result(None, tuple(exc.diagnostics))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Result(
            None,
            (
                _error(
                    "binding.corrupt",
                    f"binding payload cannot be decoded: {exc}",
                    artifact,
                ),
            ),
        )
    loaded = load_record(binding_data, ExperimentBinding)
    if loaded.value is None:
        return Result(None, loaded.diagnostics)
    binding = loaded.value
    if not isinstance(record_data, dict):
        return Result(
            None,
            (
                _error(
                    "binding.corrupt", "record snapshots must be an object", artifact
                ),
            ),
        )
    records: dict[RevisionRef, Record] = {}
    findings: list[Diagnostic] = []
    for key, payload in record_data.items():
        try:
            ref = RevisionRef.parse(key)
        except ValueError:
            findings.append(
                _error(
                    "binding.record_invalid",
                    f"invalid record reference {key!r}",
                    artifact,
                )
            )
            continue
        record_type = _KINDS.get(ref.entity.kind)
        if record_type is None:
            findings.append(
                _error(
                    "binding.record_invalid",
                    f"unsupported record kind {ref.entity.kind!r}",
                    artifact,
                )
            )
            continue
        item = load_record(payload, record_type)
        if item.value is None:
            findings.extend(item.diagnostics)
        elif revision_of(item.value) != ref:
            findings.append(
                _error(
                    "binding.record_mismatch",
                    f"historical record {ref} changed after loading",
                    artifact,
                )
            )
        else:
            records[ref] = item.value
    findings.extend(_validate_snapshots(binding, records))
    if not has_errors(findings):
        findings.extend(_verify_artifacts(store, _artifacts(binding, records)))
    return Result.of(ReplayedBinding(binding, records), findings)
