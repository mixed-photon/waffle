"""Fast recovery/invalidation acceptance without optional backend imports."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
    ObservableKind,
)
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
    FittedCoefficients,
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.layout.layout import Layout
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import Applicability, OpticalScattering
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.attempt import AttemptState
from waffle.contracts.results.provenance import OperationInfo
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.experiments.bindings.resolve import resolve_binding
from waffle.experiments.bindings.store import publish_binding, replay_binding
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common.examples import request, straight_waveguide
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.common.run_fingerprint import (
    RunFingerprint,
    compare_run_fingerprints,
    model_evaluation_fingerprint,
    run_fingerprint,
)

ENVIRONMENT = {
    "python": "3.13",
    "platform": "test",
    "solver": "1",
    "precision": "float64",
    "device": "cpu",
}


def fixture() -> tuple[TechnologyRevision, Layout, PhysicalProblem, AnalysisRequest]:
    technology = synthetic_soi()
    layout = straight_waveguide()
    problem = prepare_physical_problem(layout, technology, request("2d")).unwrap()
    analysis = AnalysisRequest(
        id=EntityId("analysis_request", "guide"),
        problem=revision_of(problem),
        excitations=(
            Excitation(kind="point", center_dbu=(0, 0), wavelength_pm=1550000),
        ),
        observables=(
            Observable(
                name="field", kind=ObservableKind.RAW_FIELD, field_component="Hz"
            ),
        ),
        numerical=NumericalSettings(grid_spacing_pm=20000, time_step_as=10, steps=100),
        backend_settings=BackendSettings(backend="fake", version="1", schema_version=1),
    )
    return technology, layout, problem, analysis


def key(
    problem: PhysicalProblem,
    analysis: AnalysisRequest,
    environment: Mapping[str, str] = ENVIRONMENT,
) -> RunFingerprint:
    return run_fingerprint(
        problem, analysis, operation_version="1", effective_environment=environment
    )


def test_dependency_changes_have_inspectable_reasons() -> None:
    _, _, problem, analysis = fixture()
    original = key(problem, analysis)
    cosmetic = problem.model_copy(
        update={
            "metadata": {"title": "renamed"},
            "id": EntityId("physical_problem", "renamed"),
        }
    )
    cosmetic_request = analysis.model_copy(
        update={"problem": revision_of(cosmetic), "metadata": {"owner": "designer"}}
    )
    compared = compare_run_fingerprints(original, key(cosmetic, cosmetic_request))
    assert compared.numerically_equivalent and not compared.exact_replay_identity
    assert "hydration is not implemented" in compared.reason
    variants = {
        "numerical": analysis.model_copy(
            update={
                "numerical": analysis.numerical.model_copy(
                    update={"grid_spacing_pm": 10000}
                )
            }
        ),
        "backend": analysis.model_copy(
            update={
                "backend_settings": analysis.backend_settings.model_copy(
                    update={"version": "2"}
                )
            }
        ),
        "excitations": analysis.model_copy(
            update={
                "excitations": (
                    analysis.excitations[0].model_copy(
                        update={"wavelength_pm": 1560000}
                    ),
                )
            }
        ),
        "observables": analysis.model_copy(
            update={
                "observables": (
                    analysis.observables[0].model_copy(
                        update={"field_component": "Ez"}
                    ),
                )
            }
        ),
    }
    for component, variant in variants.items():
        comparison = compare_run_fingerprints(original, key(problem, variant))
        assert not comparison.numerically_equivalent
        assert comparison.changed_components == (component,)
        assert component in comparison.reason
    changed_environment = key(problem, analysis, {**ENVIRONMENT, "solver": "2"})
    assert compare_run_fingerprints(
        original, changed_environment
    ).changed_components == ("environment",)
    changed_operation = run_fingerprint(
        problem, analysis, operation_version="2", effective_environment=ENVIRONMENT
    )
    assert compare_run_fingerprints(original, changed_operation).changed_components == (
        "operation",
    )
    with pytest.raises(ValueError, match="does not pin"):
        key(cosmetic, analysis)


def test_geometry_material_and_coordinate_grid_invalidate_physics() -> None:
    technology, layout, problem, analysis = fixture()
    original = key(problem, analysis)
    longer = layout.model_copy(
        update={
            "cells": (
                layout.cells[0].model_copy(
                    update={
                        "shapes": {
                            "WG": (
                                layout.cells[0]
                                .shapes["WG"][0]
                                .model_copy(update={"hi": (11000, 250)}),
                            )
                        },
                        "ports": (
                            layout.cells[0].ports[0],
                            layout.cells[0]
                            .ports[1]
                            .model_copy(update={"center": (11000, 0)}),
                        ),
                    }
                ),
            )
        }
    )
    # Extend both geometry and its terminal reference plane.
    geometry = prepare_physical_problem(longer, technology, request("2d")).unwrap()
    material = technology.model_copy(
        update={
            "materials": tuple(
                m.model_copy(
                    update={"optical": m.optical.model_copy(update={"n": 3.5})}
                )
                if m.name == "si"
                else m
                for m in technology.materials
            )
        }
    )
    changed_material = prepare_physical_problem(
        layout, material, request("2d")
    ).unwrap()
    changed_grid = problem.model_copy(update={"dbu_pm": 500})
    for changed in (geometry, changed_material, changed_grid):
        analysis_changed = analysis.model_copy(update={"problem": revision_of(changed)})
        assert compare_run_fingerprints(
            original, key(changed, analysis_changed)
        ).changed_components == ("physics",)


def test_concurrent_publication_has_one_complete_artifact_and_all_attempts(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    operation = OperationInfo(
        name="recovery.concurrent", version="1", parameters_digest=content_hash({})
    )

    def publish(index: int) -> ArtifactRef:
        with store.attempt(operation) as session:
            writer = session.new_artifact("recovery")
            writer.write_bytes("payload", b"same complete content")
            return writer.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        artifacts = list(pool.map(publish, range(16)))
    assert len(set(artifacts)) == 1
    artifact = artifacts[0]
    assert store.verify(artifact) == ()
    assert len(store.provenance_of(artifact)) == 16
    assert all(a.state == AttemptState.SUCCEEDED for a in store.list_attempts())
    assert store.read_bytes(artifact, "payload") == b"same complete content"


def test_abrupt_worker_death_leaves_staging_and_retry_keeps_history(
    tmp_path: Path,
) -> None:
    # No exception unwinding: emulate a killed worker after durable staging.
    code = """import os,sys
from pathlib import Path
from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.results.provenance import OperationInfo
from waffle.contracts.foundation.canonical import content_hash
s=LocalArtifactStore(Path(sys.argv[1]))
a=s.start_attempt(OperationInfo(name="recovery.worker",version="1",parameters_digest=content_hash({})))
w=s.open_writer(a.start.attempt_id,"recovery")
w.write_bytes("payload",b"partial")
os._exit(17)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert completed.returncode == 17
    store = LocalArtifactStore(tmp_path)
    interrupted = store.list_attempts()[0]
    assert interrupted.state == AttemptState.STARTED and interrupted.partial_outputs
    assert store.list_artifacts() == ()
    with store.attempt(interrupted.start.operation) as session:
        writer = session.new_artifact("recovery")
        writer.write_bytes("payload", b"complete")
        artifact = writer.commit()
    assert store.get_attempt(interrupted.start.attempt_id) == interrupted
    assert len(store.list_attempts()) == 2
    assert store.verify(artifact) == ()
    assert store.read_bytes(artifact, "payload") == b"complete"


def test_coefficients_invalidate_evaluation_and_historical_binding_remains_pinned(
    tmp_path: Path,
) -> None:
    technology, layout, problem, analysis = fixture()
    names = ("o1", "o2")
    interface = InterfaceDefinition(
        id=EntityId("interface", "recovery"),
        terminals=tuple(Terminal(name=n, domain=Domain.OPTICAL) for n in names),
    )
    component = ComponentDefinition(
        id=EntityId("component", "recovery"), interface=revision_of(interface)
    )
    implementation = PhysicalImplementation(
        id=EntityId("implementation", "recovery"),
        component=revision_of(component),
        source=GeneratorSource(name="example", version="1"),
        technology=TechnologyRequirement(technology=technology.id, layers=("WG",)),
    )
    design = ResolvedDesign(
        id=EntityId("design", "recovery"),
        implementation=revision_of(implementation),
        technology=revision_of(technology),
        parameters=DesignValues(),
    )
    model = ModelRevision(
        id=EntityId("model", "recovery"),
        interface=revision_of(interface),
        designs=(revision_of(design),),
        payload=EvaluatorPayload(name="example", version="1"),
        operating_conditions=OperatingConditionSchema(
            parameters=(
                ParameterSpec(
                    name="frequency",
                    dimension=Dimension.FREQUENCY,
                    default=Quantity(value=193, unit="THz"),
                ),
            )
        ),
        coefficients=FittedCoefficients(values={"neff": Quantity(value=2.4, unit="1")}),
        capability=OpticalScattering(observed_channels=(("o2", "o1"),)),
        applicability_spec=Applicability(),
    )
    mapping = ViewMapping(
        id=EntityId("view_mapping", "recovery"),
        interface=revision_of(interface),
        layout=revision_of(layout),
        problem=revision_of(problem),
        model=revision_of(model),
        bindings=tuple(
            ChannelBinding(
                terminal=p.name,
                layout_port=p.name,
                simulation_port=p.name,
                model_channel=p.name,
                anchor=PortAnchor(center=p.center, orientation=p.orientation),
            )
            for p in layout.cells[0].ports
        ),
        responses=(
            ResponseChannel(
                output="o2", excitation="o1", status=ResponseStatus.COMPUTED
            ),
        ),
    )
    circuit = Circuit(
        id=EntityId("circuit", "recovery"),
        instances=(CircuitInstance(name="guide", design=revision_of(design)),),
    )
    result = resolve_binding(
        EntityId("experiment_binding", "recovery"),
        circuit,
        {revision_of(design): design},
        {revision_of(implementation): implementation},
        {revision_of(component): component},
        {revision_of(interface): interface},
        {"guide": (model,)},
        {revision_of(model): mapping},
        {"guide": OperatingValues()},
        {"guide": (("o2", "o1"),)},
    )
    assert result.binding is not None, result.diagnostics
    snapshots = (
        technology,
        layout,
        problem,
        interface,
        component,
        implementation,
        design,
        model,
        mapping,
        circuit,
    )
    store = LocalArtifactStore(tmp_path)
    artifact = publish_binding(store, result.binding, snapshots).unwrap()
    before = key(problem, analysis)
    newer = model.model_copy(
        update={
            "coefficients": FittedCoefficients(
                values={"neff": Quantity(value=2.5, unit="1")}
            )
        }
    )
    assert model_evaluation_fingerprint(
        model, OperatingValues(), mapping
    ) != model_evaluation_fingerprint(newer, OperatingValues(), mapping)
    # Coefficients are not inputs to an electromagnetic run of fixed geometry/materials.
    assert key(problem, analysis) == before
    replayed = replay_binding(store, artifact).unwrap()
    assert replayed.binding.instances[0].model == revision_of(model)
    assert revision_of(newer) not in replayed.records
    assert replayed.records[revision_of(model)].coefficients.values["neff"].value == 2.4
