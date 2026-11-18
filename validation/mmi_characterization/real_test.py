"""Real BeamZ acceptance for the qualified, exact-sample partial MMI model."""

from __future__ import annotations

import json
import os
from pathlib import Path

from waffle_pdk_demo import mmi

from apps.cli.lifecycle import reproduce_experiment
from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.definition import InterfaceDefinition
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.mappings.mapping import ResponseStatus, ViewMapping
from waffle.contracts.models.model import ModelRevision
from waffle.contracts.physics.approximations import Polarization
from waffle.experiments.bindings.resolve import resolve_binding
from waffle.experiments.bindings.store import publish_binding, replay_binding
from waffle.models.evaluate import evaluate_optical_scattering
from waffle.simulation.photonics.characterization.mmi import (
    EVALUATOR_KEY,
    characterize_mmi,
    sample_evaluator,
)


def test_measured_mmi() -> None:
    root = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    store = LocalArtifactStore(root / "store")
    device = mmi(polarization=Polarization.TM)
    result = characterize_mmi(
        device.problem,
        device.model,
        device.mapping,
        command=(str(Path("third_party/beamz/worker").resolve()),),
        root=root / "workers",
        store=store,
    )
    summary = {
        "passed": result.report.passed,
        "model": str(revision_of(result.model)) if result.model is not None else None,
        "model_artifact": str(result.model_artifact),
        "evidence": str(result.evidence),
        "validation": str(result.validation),
        "workers": {
            name: [
                {
                    "attempt": run.attempt_id,
                    "artifact": str(run.artifact),
                    "grid_spacing_m": run.effective["dx_m"],
                    "acquisitions": len(run.run.acquisitions),
                }
                for run in runs
            ]
            for name, runs in result.executions.items()
        },
        "transfer": json.loads(store.read_bytes(result.evidence, "transfer.json")),
        "report": json.loads(store.read_bytes(result.validation, "report.json")),
    }
    summary_path = root / "measured.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    assert set(result.executions) == {"fine", "coarse", "time", "boundary"}
    for runs in result.executions.values():
        assert len(runs) == 2
        for run in runs:
            assert run.artifact is not None
            assert len(run.run.acquisitions) == 16
    assert result.report.passed, result.report.comparisons
    assert result.model is not None and result.mapping is not None
    assert result.model_artifact is not None
    assert (
        ModelRevision.model_validate_json(
            store.read_bytes(result.model_artifact, "model.json")
        )
        == result.model
    )

    observed = (("o3", "o1"), ("o4", "o1"))
    comparisons = {
        check.quantity: check.comparison for check in result.report.comparisons
    }
    for variant, kind in (
        ("coarse", "grid_convergence"),
        ("time", "time_convergence"),
        ("boundary", "boundary_convergence"),
        ("window", "analytic_reference"),
        ("mirror", "analytic_reference"),
    ):
        for output, excitation in observed:
            for quantity in ("magnitude", "wrapped_phase"):
                assert (
                    comparisons[f"{variant}.{output}/{excitation}.{quantity}"] == kind
                )
    assert "guided_column_power_excess" in comparisons
    for variant in (*result.executions, "window"):
        for quantity in (
            "incident_condition_number",
            "mirror_launch_complex_relative_error",
            "right_boundary_incident_fraction",
            "standing_wave_fit_residual",
            "modal_index_search_at_limit",
        ):
            assert f"{variant}.{quantity}" in comparisons
    channels = ("o1", "o2", "o3", "o4")
    uncomputed = {(out, exc) for out in channels for exc in channels} - set(observed)
    assert result.report.observed_channels == observed
    assert set(result.report.uncomputed_channels) == uncomputed
    assert result.model.capability.observed_channels == observed
    assert {
        (item.output, item.excitation): item.status for item in result.mapping.responses
    } == {
        pair: ResponseStatus.COMPUTED if pair in observed else ResponseStatus.UNCOMPUTED
        for pair in (*observed, *uncomputed)
    }

    circuit, _ = device.bind()
    model_ref = revision_of(result.model)
    selected = resolve_binding(
        EntityId("experiment_binding", "mmi_simulated"),
        circuit,
        {revision_of(device.design): device.design},
        {revision_of(device.implementation): device.implementation},
        {revision_of(device.component): device.component},
        {revision_of(device.interface): device.interface},
        {"device": (device.model, result.model)},
        {revision_of(device.model): device.mapping, model_ref: result.mapping},
        {"device": device.operating()},
        {"device": observed},
        selected={"device": model_ref},
    )
    assert selected.binding is not None, (selected.diagnostics, selected.decisions)
    binding = publish_binding(
        store, selected.binding, (*device.snapshots(), result.model, result.mapping)
    ).unwrap()

    replay = replay_binding(LocalArtifactStore(root / "store"), binding).unwrap()
    assert replay.binding == selected.binding
    (entry,) = replay.binding.instances
    assert entry.model == model_ref
    assert entry.mapping == revision_of(result.mapping)
    assert (entry.evaluator_name, entry.evaluator_version) == EVALUATOR_KEY
    model, interface, mapping, design = (
        replay.records[ref]
        for ref in (entry.model, entry.interface, entry.mapping, entry.design)
    )
    assert isinstance(model, ModelRevision)
    assert isinstance(interface, InterfaceDefinition)
    assert isinstance(mapping, ViewMapping)
    assert isinstance(design, ResolvedDesign)
    evaluators = {EVALUATOR_KEY: sample_evaluator(store, model)}
    evaluated = evaluate_optical_scattering(
        model,
        interface,
        entry.operating,
        mapping,
        entry.requested_channels,
        evaluators,
        design=design,
    ).unwrap()
    assert evaluated == {
        (item["output"], item["excitation"]): complex(item["real"], item["imag"])
        for item in summary["transfer"]["observed"]
    }
    (response,) = reproduce_experiment(store, binding).unwrap()
    assert response.instance == entry.instance
    assert response.model == model_ref
    assert response.response == evaluated
    for pair in uncomputed:
        rejected = evaluate_optical_scattering(
            model,
            interface,
            entry.operating,
            mapping,
            (pair,),
            evaluators,
            design=design,
        )
        assert not rejected.ok
        assert {"model.channel_unobserved", "model.channel_uncomputed"} <= {
            diagnostic.code for diagnostic in rejected.diagnostics
        }
    off_sample = evaluate_optical_scattering(
        model,
        interface,
        device.operating(1.551),
        mapping,
        observed,
        evaluators,
        design=design,
    )
    assert not off_sample.ok
    summary["replay"] = {
        "binding": str(binding),
        "requested_channels": entry.requested_channels,
        "rejected_uncomputed_channels": sorted(uncomputed),
        "rejected_off_sample_wavelength_nm": 1551,
        "responses": [
            {"output": out, "excitation": exc, "real": value.real, "imag": value.imag}
            for (out, exc), value in sorted(evaluated.items())
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
