"""Application assembly for the installed straight-waveguide reference lifecycle."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from waffle.artifacts.local import LocalArtifactStore
from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.characterization.calibration import power
from waffle.contracts.component.definition import InterfaceDefinition
from waffle.contracts.component.design import (
    Circuit,
    ResolvedDesign,
    SampleIdentity,
    Specimen,
)
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import Record, revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.dataset import (
    Calibration,
    MeasurementPayload,
    PowerObservation,
    WavelengthAxis,
)
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.experiments.bindings.resolve import resolve_binding
from waffle.experiments.bindings.store import publish_binding, replay_binding
from waffle.experiments.measurements.ingest import import_measurement
from waffle.layout.formats.publish import publish_package
from waffle.models.evaluate import (
    ChannelPair,
    OpticalEvaluator,
    ScatteringResponse,
    evaluate_optical_scattering,
)
from waffle.simulation.common.backend import CancellationToken
from waffle.simulation.photonics.characterization import mmi, publication, recipe

if TYPE_CHECKING:
    from waffle_pdk_demo import ReferenceComponent

type ModelChoice = Literal["analytic", "simulated", "calibrated"]

_PAIR: ChannelPair = ("o2", "o1")
_WAVELENGTH_PM = 1_550_000


@dataclass(frozen=True)
class InstanceResponse:
    instance: str
    model: RevisionRef
    response: ScatteringResponse

    def as_dict(self) -> dict[str, object]:
        return {
            "instance": self.instance,
            "model": str(self.model),
            "channels": [
                {
                    "output": output,
                    "excitation": excitation,
                    "real": value.real,
                    "imag": value.imag,
                }
                for (output, excitation), value in sorted(self.response.items())
            ],
        }


@dataclass(frozen=True)
class ReferenceRun:
    """Immutable report and the exact experiment bindings produced by a run."""

    artifact: ArtifactRef
    analytic_binding: ArtifactRef
    selected_binding: ArtifactRef
    report: dict[str, object]


def _error(code: str, message: str, subject: str | None = None) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject
    )


def _require[T](result: Result[T], diagnostics: list[Diagnostic]) -> T:
    if result.value is None:
        raise ArtifactError(*result.diagnostics)
    diagnostics.extend(result.diagnostics)
    return result.value


def _evaluator(
    store: ArtifactStore, model: ModelRevision
) -> tuple[tuple[str, str], OpticalEvaluator]:
    if isinstance(payload := model.payload, EvaluatorPayload):
        key = (payload.name, payload.version)
        if key == publication.EVALUATOR_KEY:
            return key, publication.sample_evaluator(store, model)
        if key == mmi.EVALUATOR_KEY:
            return key, mmi.sample_evaluator(store, model)
        if key == power.EVALUATOR_KEY:
            return key, _require(power.calibrated_evaluator(store, model), [])
        if key[0] == "waffle.demo.analytic":
            from waffle_pdk_demo import VERSION, analytic_evaluator

            if key[1] == VERSION:
                return key, analytic_evaluator
    raise ArtifactError(
        _error(
            "reference.evaluator_unavailable",
            "install the pinned evaluator for this model",
            str(revision_of(model)),
        )
    )


def reproduce_experiment(
    store: ArtifactStore, binding: ArtifactRef
) -> Result[tuple[InstanceResponse, ...]]:
    """Verify the stored binding and evaluate each instance with the exact recorded inputs."""
    diagnostics: list[Diagnostic] = []
    try:
        replay = _require(replay_binding(store, binding), diagnostics)
        results: list[InstanceResponse] = []
        for entry in replay.binding.instances:
            model, interface, mapping, design = (
                replay.records[ref]
                for ref in (entry.model, entry.interface, entry.mapping, entry.design)
            )
            assert isinstance(model, ModelRevision)
            assert isinstance(interface, InterfaceDefinition)
            assert isinstance(mapping, ViewMapping)
            assert isinstance(design, ResolvedDesign)
            key, evaluate = _evaluator(store, model)
            response = _require(
                evaluate_optical_scattering(
                    model,
                    interface,
                    entry.operating,
                    mapping,
                    entry.requested_channels,
                    {key: evaluate},
                    design=design,
                ),
                diagnostics,
            )
            results.append(InstanceResponse(entry.instance, entry.model, response))
    except ArtifactError as exc:
        return Result(None, tuple(dict.fromkeys([*diagnostics, *exc.diagnostics])))
    except ImportError as exc:
        return Result(
            None,
            (
                _error(
                    "reference.package_missing",
                    f"install the matching demo PDK package: {exc}",
                ),
            ),
        )
    return Result.of(tuple(results), dict.fromkeys(diagnostics))


def _binding(
    store: ArtifactStore,
    device: ReferenceComponent,
    circuit: Circuit,
    choice: ModelChoice,
    model: ModelRevision,
    mapping: ViewMapping,
    operating: OperatingValues,
    snapshots: tuple[Record, ...],
    diagnostics: list[Diagnostic],
) -> ArtifactRef:
    chosen = revision_of(model)
    resolved = resolve_binding(
        EntityId("experiment_binding", f"waveguide_{choice}"),
        circuit,
        {revision_of(device.design): device.design},
        {revision_of(device.implementation): device.implementation},
        {revision_of(device.component): device.component},
        {revision_of(device.interface): device.interface},
        {"device": (device.model, model)},
        {revision_of(device.model): device.mapping, chosen: mapping},
        {"device": operating},
        {"device": (_PAIR,)},
        selected={"device": chosen},
    )
    if resolved.binding is None:
        raise ArtifactError(
            *resolved.diagnostics,
            *(
                diagnostic
                for decision in resolved.decisions
                if decision.model == chosen
                for diagnostic in decision.diagnostics
            ),
        )
    return _require(
        publish_binding(store, resolved.binding, (*snapshots, model, mapping)),
        diagnostics,
    )


def _synthetic_capture(
    store: ArtifactStore,
    device: ReferenceComponent,
    mapping: ViewMapping,
    name: str,
    transmitted_power_mw: float,
    diagnostics: list[Diagnostic],
) -> power.Capture:
    design = revision_of(device.design)
    specimen = Specimen(
        id=EntityId("specimen", name),
        design=design,
        sample=SampleIdentity(lot="synthetic_demo", site=name),
    )
    source = MeasurementPayload(
        specimen=revision_of(specimen),
        design=design,
        mapping=revision_of(mapping),
        synthetic=True,
        axis=WavelengthAxis(values=(_WAVELENGTH_PM / 1000,), unit="nm"),
        observations=(
            PowerObservation(
                output_channel=_PAIR[0],
                excitation_channel=_PAIR[1],
                values=(transmitted_power_mw,),
                unit="mW",
            ),
        ),
        conditions={"incident_power": Quantity(value=1, unit="mW")},
        calibration=Calibration(
            state="calibrated",
            method="synthetic_power_fixture_v1",
            reference="incident 1 mW at input reference plane",
        ),
    )
    dataset = _require(
        import_measurement(
            source.model_dump_json().encode(),
            id=EntityId("measurement_dataset", name),
            specimen=specimen,
            mapping=mapping,
            problem=device.problem,
            store=store,
        ),
        diagnostics,
    )
    return power.Capture(dataset, specimen)


def run_reference(
    root: Path,
    *,
    choice: ModelChoice = "analytic",
    worker_command: tuple[str, ...] | None = None,
    cancellation: CancellationToken | None = None,
) -> Result[ReferenceRun]:
    """Run the released 2 µm demonstration at one 1.55 µm TM sample."""
    diagnostics: list[Diagnostic] = []
    if choice not in ("analytic", "simulated", "calibrated"):
        raise ValueError(f"unknown reference model choice {choice!r}")
    if choice != "analytic" and worker_command is None:
        if importlib.util.find_spec("beamz") is None:
            return Result(
                None,
                (
                    _error(
                        "reference.backend_missing",
                        "install waffle-eda[beamz] or select a BeamZ worker environment",
                        "beamz",
                    ),
                ),
            )
        worker_command = (sys.executable, "-m", "third_party.beamz.worker")
    try:
        from waffle_pdk_demo import waveguide

        try:
            device = waveguide(
                length_um=2.0, wavelength_um=1.55, polarization=Polarization.TM
            )
        except ValueError as exc:
            raise ArtifactError(
                _error("reference.design_failed", str(exc), "design:straight")
            ) from exc
        root = root.resolve()
        store = LocalArtifactStore(root)
        gds = _require(
            publish_package(store, _require(device.gds_package(), diagnostics)),
            diagnostics,
        )
        circuit, analytic = device.bind()
        snapshots = device.snapshots()
        analytic_binding = _require(
            publish_binding(store, analytic, snapshots), diagnostics
        )
        analytic_response = _require(
            reproduce_experiment(store, analytic_binding), diagnostics
        )

        selected = analytic_binding
        model, mapping = device.model, device.mapping
        references = {"gds": gds, "analytic_binding": analytic_binding}
        comparisons: list[dict[str, object]] = []
        attempts: list[str] = []
        frequency = power.LIGHT_SPEED_M_S / (_WAVELENGTH_PM * 1e-12)
        operating = OperatingValues(
            values={"frequency": Quantity(value=frequency, unit="Hz")}
        )
        transmitted = 1.0
        gain: float | None = None
        validation_power_error: float | None = None
        if choice != "analytic":
            assert worker_command is not None
            try:
                characterized = recipe.characterize_waveguide(
                    device.problem,
                    model,
                    mapping,
                    command=worker_command,
                    root=root / "workers",
                    store=store,
                    cancellation=cancellation,
                )
            except ValueError as exc:
                raise ArtifactError(
                    _error(
                        "reference.characterization_failed",
                        str(exc),
                        str(device.problem.id),
                    )
                ) from exc
            published = characterized.publication
            if (
                published.model is None
                or characterized.mapping is None
                or published.model_artifact is None
            ):
                raise ArtifactError(
                    _error(
                        "reference.qualification_failed",
                        f"see transfer {published.evidence} and report {published.validation}",
                        str(device.problem.id),
                    )
                )
            model, mapping = published.model, characterized.mapping
            references.update(
                transfer=published.evidence,
                validation=published.validation,
                simulated_model=published.model_artifact,
            )
            comparisons = [
                item.model_dump(mode="json")
                for item in characterized.report.comparisons
            ]
            attempts = [execution.attempt_id for execution in characterized.executions]
            evaluator_key, evaluator = _evaluator(store, model)
            evaluated = _require(
                evaluate_optical_scattering(
                    model,
                    device.interface,
                    operating,
                    mapping,
                    (_PAIR,),
                    {evaluator_key: evaluator},
                    design=device.design,
                ),
                diagnostics,
            )
            transmitted = min(1.0, abs(evaluated[_PAIR]) ** 2)
            snapshots = (*snapshots, model, mapping)

        # Distinct synthetic captures exercise fitting and validation without implying measured device data.
        fitting = _synthetic_capture(
            store, device, mapping, "synthetic_fit", 0.78 * transmitted, diagnostics
        )
        validation = _synthetic_capture(
            store, device, mapping, "synthetic_check", 0.784 * transmitted, diagnostics
        )
        references.update(
            fitting_data=fitting.dataset.raw_artifact,
            validation_data=validation.dataset.raw_artifact,
        )
        snapshots = (
            *snapshots,
            fitting.specimen,
            fitting.dataset,
            validation.specimen,
            validation.dataset,
        )
        if choice == "calibrated":
            key, evaluate = _evaluator(store, model)
            calibrated = _require(
                power.calibrate_power_gain(
                    store,
                    base=model,
                    mapping=mapping,
                    interface=device.interface,
                    design=device.design,
                    problem=device.problem,
                    fitting=fitting,
                    validation=validation,
                    pair=_PAIR,
                    spec=power.PowerFitSpec(validation_max_error=0.02),
                    evaluators={key: evaluate},
                ),
                diagnostics,
            )
            model, mapping = calibrated.model, calibrated.mapping
            gain = calibrated.report.power_gain
            validation_power_error = calibrated.report.validation_error
            references["calibration"] = calibrated.artifact
        if choice != "analytic":
            selected = _binding(
                store,
                device,
                circuit,
                choice,
                model,
                mapping,
                operating,
                snapshots,
                diagnostics,
            )
        reproduced = _require(reproduce_experiment(store, selected), diagnostics)
        references["selected_binding"] = selected
        report: dict[str, object] = {
            "schema_version": 1,
            "model_choice": choice,
            "references": {role: str(ref) for role, ref in references.items()},
            "design": str(revision_of(device.design)),
            "technology": str(revision_of(device.technology)),
            "layout": str(revision_of(device.layout)),
            "physics": str(revision_of(device.problem)),
            "mapping": str(revision_of(mapping)),
            "model": str(revision_of(model)),
            "wavelength_pm": _WAVELENGTH_PM,
            "polarization": device.polarization.value,
            "fit_dataset": str(revision_of(fitting.dataset)),
            "validation_dataset": str(revision_of(validation.dataset)),
            "synthetic_measurements": True,
            "power_gain": gain,
            "validation_power_error": validation_power_error,
            "numerical_comparisons": comparisons,
            "worker_attempts": attempts,
            "analytic_responses": [item.as_dict() for item in analytic_response],
            "replayed_responses": [item.as_dict() for item in reproduced],
        }
        operation = OperationInfo(
            name="reference.waveguide",
            version="1",
            parameters_digest=content_hash(
                {"choice": choice, "wavelength_pm": _WAVELENGTH_PM}
            ),
        )
        with store.attempt(
            operation,
            tuple(InputLink(role=role, target=ref) for role, ref in references.items()),
        ) as session:
            writer = session.new_artifact("reference_workflow")
            writer.write_json("report.json", report)
            writer.write_json(
                "measurements.json",
                [
                    record.model_dump(mode="json")
                    for record in (
                        fitting.specimen,
                        fitting.dataset,
                        validation.specimen,
                        validation.dataset,
                    )
                ],
            )
            artifact = writer.commit()
    except ArtifactError as exc:
        return Result(None, tuple(dict.fromkeys([*diagnostics, *exc.diagnostics])))
    except ImportError as exc:
        return Result(
            None,
            (
                _error(
                    "reference.package_missing",
                    f"install waffle-pdk-demo and the GDSFactory extra: {exc}",
                ),
            ),
        )
    except OSError as exc:
        return Result(None, (_error("reference.io", str(exc), str(root)),))
    return Result.of(
        ReferenceRun(artifact, analytic_binding, selected, report),
        dict.fromkeys(diagnostics),
    )
