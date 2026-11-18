from __future__ import annotations

from pathlib import Path

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.definition import (
    ComponentDefinition,
    Domain,
    GeneratorSource,
    InterfaceDefinition,
    PhysicalImplementation,
    TechnologyRequirement,
    Terminal,
)
from waffle.contracts.component.design import Circuit, CircuitInstance, ResolvedDesign
from waffle.contracts.component.parameters import (
    DesignValues,
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.layout.layout import Cell, LayerSpec, Layout, Port
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import Applicability, OpticalScattering
from waffle.contracts.models.model import (
    ArtifactPayload,
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.physics.problem import (
    Boundaries3D,
    BoundaryCondition,
    Domain3D,
    Extrusion3D,
    PhysicalProblem,
    Port3D,
)
from waffle.contracts.results.provenance import OperationInfo
from waffle.experiments.bindings.resolve import resolve_binding
from waffle.experiments.bindings.store import publish_binding, replay_binding
from waffle.pdk.testing.synthetic import synthetic_soi


def _fixture() -> tuple[dict[str, object], tuple[object, ...]]:
    technology = synthetic_soi()
    layout = Layout(
        id=EntityId("layout", "wg"),
        grid=Grid(dbu_pm=1000),
        top="wg",
        layers=(LayerSpec(name="WG", layer=1, datatype=0),),
        cells=(
            Cell(
                name="wg",
                ports=(
                    Port(
                        name="o1",
                        center=(0, 0),
                        width=500,
                        orientation=180,
                        layer="WG",
                        domain=Domain.OPTICAL,
                    ),
                    Port(
                        name="o2",
                        center=(10000, 0),
                        width=500,
                        orientation=0,
                        layer="WG",
                        domain=Domain.OPTICAL,
                    ),
                ),
            ),
        ),
    )
    boundaries = Boundaries3D(
        x_min=BoundaryCondition(),
        x_max=BoundaryCondition(),
        y_min=BoundaryCondition(),
        y_max=BoundaryCondition(),
        z_min=BoundaryCondition(),
        z_max=BoundaryCondition(),
    )
    domain = Domain3D(
        lo=(-1000, -1000),
        hi=(11000, 1000),
        z_min_pm=-1000,
        z_max_pm=1000,
        boundaries=boundaries,
    )
    ports = (
        Port3D(
            path="o1",
            center=(0, 0),
            width=500,
            orientation=180,
            layer="WG",
            domain=Domain.OPTICAL,
            z_min_pm=0,
            z_max_pm=1000,
        ),
        Port3D(
            path="o2",
            center=(10000, 0),
            width=500,
            orientation=0,
            layer="WG",
            domain=Domain.OPTICAL,
            z_min_pm=0,
            z_max_pm=1000,
        ),
    )
    problem = PhysicalProblem(
        id=EntityId("physical_problem", "wg"),
        layout=revision_of(layout),
        top_cell="wg",
        technology=revision_of(technology),
        dbu_pm=1000,
        description=Extrusion3D(
            domain=domain,
            materials=technology.materials,
            background=technology.materials[0].name,
            films=(),
            prisms=(),
            ports=ports,
        ),
    )
    interface = InterfaceDefinition(
        id=EntityId("interface", "wg"),
        terminals=(
            Terminal(name="o1", domain=Domain.OPTICAL),
            Terminal(name="o2", domain=Domain.OPTICAL),
        ),
    )
    component = ComponentDefinition(
        id=EntityId("component", "wg"), interface=revision_of(interface)
    )
    implementation = PhysicalImplementation(
        id=EntityId("implementation", "wg"),
        component=revision_of(component),
        source=GeneratorSource(name="wg", version="1"),
        technology=TechnologyRequirement(technology=technology.id),
    )
    design = ResolvedDesign(
        id=EntityId("design", "wg"),
        implementation=revision_of(implementation),
        parameters=DesignValues(),
        technology=revision_of(technology),
    )
    circuit = Circuit(
        id=EntityId("circuit", "two_guides"),
        instances=(
            CircuitInstance(name="left", design=revision_of(design)),
            CircuitInstance(name="right", design=revision_of(design)),
        ),
    )
    models = tuple(
        ModelRevision(
            id=EntityId("model", "wg"),
            interface=revision_of(interface),
            implementations=(revision_of(implementation),),
            designs=(revision_of(design),),
            operating_conditions=OperatingConditionSchema(
                parameters=(
                    ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),
                )
            ),
            payload=EvaluatorPayload(name="wg", version=version),
            capability=OpticalScattering(observed_channels=(("o2", "o1"),)),
            applicability_spec=Applicability(),
        )
        for version in ("1", "2")
    )
    mappings = tuple(
        ViewMapping(
            id=EntityId("view_mapping", f"wg_{index}"),
            interface=revision_of(interface),
            layout=revision_of(layout),
            problem=revision_of(problem),
            model=revision_of(model),
            bindings=(
                ChannelBinding(
                    terminal="o1",
                    layout_port="o1",
                    anchor=PortAnchor(center=(0, 0), orientation=180),
                    simulation_port="o1",
                    model_channel="o1",
                ),
                ChannelBinding(
                    terminal="o2",
                    layout_port="o2",
                    anchor=PortAnchor(center=(10000, 0), orientation=0),
                    simulation_port="o2",
                    model_channel="o2",
                ),
            ),
            responses=(
                ResponseChannel(
                    output="o2", excitation="o1", status=ResponseStatus.COMPUTED
                ),
            ),
        )
        for index, model in enumerate(models)
    )
    records = (
        technology,
        layout,
        problem,
        interface,
        component,
        implementation,
        design,
        circuit,
        *models,
        *mappings,
    )
    context = {
        "circuit": circuit,
        "designs": {revision_of(design): design},
        "implementations": {revision_of(implementation): implementation},
        "components": {revision_of(component): component},
        "interfaces": {revision_of(interface): interface},
        "candidates": {"left": models, "right": models},
        "mappings": {
            revision_of(model): mapping for model, mapping in zip(models, mappings)
        },
        "operating": {
            name: OperatingValues(values={"frequency": Quantity(value=195, unit="THz")})
            for name in ("left", "right")
        },
        "requested_channels": {name: (("o2", "o1"),) for name in ("left", "right")},
    }
    return context, records


def _resolved(context: dict[str, object]) -> object:
    models = context["candidates"]["left"]  # type: ignore[index]
    return resolve_binding(
        EntityId("experiment_binding", "run"),
        **context,
        selected={"left": revision_of(models[0]), "right": revision_of(models[1])},
    )  # type: ignore[arg-type]


def test_same_design_instances_can_pin_different_models_and_replay(
    tmp_path: Path,
) -> None:
    context, records = _fixture()
    result = _resolved(context)
    assert result.binding is not None
    assert not result.diagnostics
    assert len({entry.model for entry in result.binding.instances}) == 2
    store = LocalArtifactStore(tmp_path)
    artifact = publish_binding(store, result.binding, records).unwrap()
    replayed = replay_binding(store, artifact).unwrap()
    assert replayed.binding == result.binding
    assert replayed.records[result.binding.circuit] == context["circuit"]
    context["candidates"] = {"left": (), "right": ()}
    assert replay_binding(store, artifact).unwrap().binding == result.binding


def test_ambiguity_and_rejected_candidate_explanation() -> None:
    context, _ = _fixture()
    ambiguous = resolve_binding(EntityId("experiment_binding", "run"), **context)  # type: ignore[arg-type]
    assert "binding.ambiguous_model" in {d.code for d in ambiguous.diagnostics}
    assert len(ambiguous.decisions) == 4
    context["operating"] = {
        name: OperatingValues(values={"frequency": Quantity(value=1, unit="um")})
        for name in ("left", "right")
    }
    rejected = resolve_binding(EntityId("experiment_binding", "run"), **context)  # type: ignore[arg-type]
    assert all(not decision.accepted for decision in rejected.decisions)
    assert any(
        d.code == "units.dimension_mismatch"
        for decision in rejected.decisions
        for d in decision.diagnostics
    )


def test_missing_snapshot_and_corrupt_artifact_are_actionable(tmp_path: Path) -> None:
    context, records = _fixture()
    result = _resolved(context)
    assert result.binding is not None
    store = LocalArtifactStore(tmp_path)
    missing = publish_binding(store, result.binding, records[:-2])
    assert not missing.ok
    assert "binding.record_missing" in {d.code for d in missing.diagnostics}
    artifact = publish_binding(store, result.binding, records).unwrap()
    path = (
        tmp_path
        / "artifacts"
        / artifact.kind
        / artifact.digest.partition(":")[2]
        / "files"
        / "records.json"
    )
    path.write_bytes(path.read_bytes() + b"corruption")
    replayed = replay_binding(store, artifact)
    assert not replayed.ok
    assert "artifact.file_corrupt" in {d.code for d in replayed.diagnostics}


def test_missing_historical_artifact_is_reported(tmp_path: Path) -> None:
    context, records = _fixture()
    result = _resolved(context)
    assert result.binding is not None
    store = LocalArtifactStore(tmp_path)
    artifact = publish_binding(store, result.binding, records).unwrap()
    manifest = (
        tmp_path
        / "artifacts"
        / artifact.kind
        / artifact.digest.partition(":")[2]
        / "manifest.json"
    )
    manifest.unlink()
    replayed = replay_binding(store, artifact)
    assert not replayed.ok
    assert "artifact.not_found" in {d.code for d in replayed.diagnostics}


def test_snapshot_hash_mismatch_does_not_reinterpret_record(tmp_path: Path) -> None:
    context, records = _fixture()
    result = _resolved(context)
    assert result.binding is not None
    snapshots = {
        str(revision_of(record)): record.model_dump(mode="json") for record in records
    }
    model_ref = str(result.binding.instances[0].model)
    snapshots[model_ref]["metadata"] = {"changed": "after pinning"}
    store = LocalArtifactStore(tmp_path)
    operation = OperationInfo(
        name="test.binding.tamper", version="1", parameters_digest=content_hash({})
    )
    with store.attempt(operation) as session:
        writer = session.new_artifact("experiment_binding")
        writer.write_json("binding.json", result.binding.model_dump(mode="json"))
        writer.write_json("records.json", snapshots)
        artifact = writer.commit()
    replayed = replay_binding(store, artifact)
    assert not replayed.ok
    assert "binding.record_mismatch" in {d.code for d in replayed.diagnostics}


def test_nested_evidence_payload_cannot_be_left_out_of_replay(tmp_path: Path) -> None:
    context, records = _fixture()
    binding = _resolved(context).binding
    assert binding is not None
    entry = binding.instances[0]
    by_ref = {revision_of(record): record for record in records}
    model = by_ref[entry.model]
    evidence = model.model_copy(
        update={
            "id": EntityId("model", "nested_evidence"),
            "payload": ArtifactPayload(artifact="model@sha256:" + "f" * 64),
        }
    )
    updated_model = model.model_copy(
        update={
            "evidence": (
                EvidenceRef(kind=EvidenceKind.SIMULATION, ref=revision_of(evidence)),
            )
        }
    )
    updated_mapping = by_ref[entry.mapping].model_copy(
        update={"model": revision_of(updated_model)}
    )
    updated_entry = entry.model_copy(
        update={
            "model": revision_of(updated_model),
            "mapping": revision_of(updated_mapping),
        }
    )
    binding = binding.model_copy(
        update={
            "instances": (updated_entry, *binding.instances[1:]),
        }
    )
    snapshots = (*records, updated_model, updated_mapping)
    store = LocalArtifactStore(tmp_path)
    missing_record = publish_binding(store, binding, snapshots)
    assert "binding.record_missing" in {d.code for d in missing_record.diagnostics}
    missing_payload = publish_binding(store, binding, (*snapshots, evidence))
    assert "artifact.not_found" in {d.code for d in missing_payload.diagnostics}
    assert not store.list_artifacts()
