"""Publish and restore the qualified exact-sample directional waveguide model."""

from __future__ import annotations

import cmath
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.characterization.report import ValidationReport
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ModalTransform,
    Normalization,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Applicability,
    AxisCoverage,
    OpticalScattering,
)
from waffle.contracts.models.model import (
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.attempt import AttemptState
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.models.evaluate import ChannelPair, OpticalEvaluator

EVALUATOR_KEY = ("waffle.waveguide.directional.samples", "1")
WAVELENGTH_PM = 1_550_000
FREQUENCY_HZ = 299_792_458.0 / (WAVELENGTH_PM * 1e-12)
_PROCEDURE = "uniform_guide_directional_two_port"
_PROCEDURE_VERSION = "1"
_ARTIFACT_KIND = "waveguide_directional_transfer"
_RUNS = ("fine", "medium", "coarse", "duration", "boundary")
_CHANNELS = ("o1", "o2")
_PAIRS = tuple((out, inc) for out in _CHANNELS for inc in _CHANNELS)
_CAPABILITY = OpticalScattering(observed_channels=_PAIRS)
_FREQUENCY = Quantity(value=FREQUENCY_HZ, unit="Hz")


@dataclass(frozen=True)
class PublishedDirectionalWaveguide:
    evidence: ArtifactRef
    validation: ArtifactRef
    model_artifact: ArtifactRef | None
    model: ModelRevision | None
    mapping: ViewMapping | None


class _Sample(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, allow_inf_nan=False
    )

    output: str
    excitation: str
    real: float
    imag: float


class _Transfer(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, allow_inf_nan=False
    )

    schema_version: int
    procedure: Literal["uniform_guide_directional_two_port"]
    procedure_version: Literal["1"]
    wavelength_pm: int
    frequency_hz: float
    polarization: Literal["tm"]
    normalization: Literal["power_wave"]
    observed: tuple[_Sample, ...]


def _sample_applicability() -> Applicability:
    return Applicability(
        axes=(AxisCoverage(parameter="frequency", sampled=(_FREQUENCY,)),)
    )


def _model_accepts_sample(model: ModelRevision) -> bool:
    schema = model.operating_conditions
    names = tuple(parameter.name for parameter in schema.parameters)
    return names == ("frequency",) and not schema.check(
        OperatingValues(values={"frequency": _FREQUENCY})
    )


def _worker_artifacts(
    store: ArtifactStore, summary: dict[str, object]
) -> tuple[ArtifactRef, ...]:
    runs = summary.get("runs")
    if not isinstance(runs, dict) or set(runs) != set(_RUNS):
        raise ValueError("directional qualification requires all five worker settings")
    artifacts: list[ArtifactRef] = []
    for setting in _RUNS:
        launches = runs[setting]
        if not isinstance(launches, (list, tuple)) or len(launches) != 2:
            raise ValueError(f"{setting} requires two directional launches")
        for launch in launches:
            if (
                not isinstance(launch, dict)
                or not isinstance(pin := launch.get("artifact"), str)
                or not isinstance(attempt_id := launch.get("attempt_id"), str)
            ):
                raise TypeError(
                    f"{setting} requires exact worker artifact and attempt pins"
                )
            artifact = ArtifactRef.parse(pin)
            attempt = store.get_attempt(attempt_id)
            outcome = attempt.outcome
            if (
                artifact.kind != "simulation_fields"
                or outcome is None
                or outcome.state is not AttemptState.SUCCEEDED
                or artifact not in outcome.produced
            ):
                raise ValueError(
                    f"{setting} worker artifact must come from its successful pinned attempt"
                )
            artifacts.append(artifact)
    if len(set(artifacts)) != len(artifacts):
        raise ValueError("each directional launch must pin a distinct worker artifact")
    return tuple(artifacts)


def _qualified_transfer(
    summary: dict[str, object], responses: Mapping[ChannelPair, complex]
) -> _Transfer:
    if set(responses) != set(_PAIRS) or any(
        not isinstance(value, complex) or not cmath.isfinite(value)
        for value in responses.values()
    ):
        raise ValueError("directional sample requires four finite complex responses")
    matrix = [
        [[responses[out, inc].real, responses[out, inc].imag] for inc in _CHANNELS]
        for out in _CHANNELS
    ]
    estimates = summary.get("estimates")
    if (
        not isinstance(estimates, dict)
        or not isinstance(fine := estimates.get("fine"), dict)
        or fine.get("matrix") != matrix
    ):
        raise ValueError("directional sample must be the unmodified fine estimate")
    return _Transfer(
        schema_version=1,
        procedure=_PROCEDURE,
        procedure_version=_PROCEDURE_VERSION,
        wavelength_pm=WAVELENGTH_PM,
        frequency_hz=FREQUENCY_HZ,
        polarization="tm",
        normalization="power_wave",
        observed=tuple(
            _Sample(
                output=out,
                excitation=inc,
                real=responses[out, inc].real,
                imag=responses[out, inc].imag,
            )
            for out, inc in _PAIRS
        ),
    )


def publish_directional_waveguide(
    store: ArtifactStore,
    template: ModelRevision,
    mapping: ViewMapping,
    report: ValidationReport,
    summary: dict[str, object],
    *,
    responses: Mapping[ChannelPair, complex],
) -> PublishedDirectionalWaveguide:
    """Publish the raw benchmark and validation, and qualify a separate model on pass."""
    if (
        mapping.model != revision_of(template)
        or mapping.interface != template.interface
        or not _model_accepts_sample(template)
        or len(mapping.bindings) != len(_CHANNELS)
        or {binding.model_channel for binding in mapping.bindings} != set(_CHANNELS)
        or any(
            binding.model_channel != binding.terminal
            or binding.model_channel != binding.layout_port
            or binding.model_channel != binding.simulation_port
            or binding.mode != 0
            or binding.modal_transform is not ModalTransform.IDENTITY
            or binding.normalization is not Normalization.POWER_WAVE
            or binding.reference_plane_offset_dbu != 0
            for binding in mapping.bindings
        )
    ):
        raise ValueError(
            "directional publication requires the original template and power-wave endpoint mapping"
        )
    if (
        report.procedure != _PROCEDURE
        or report.procedure_version != _PROCEDURE_VERSION
        or report.observed_channels != _PAIRS
        or report.uncomputed_channels
        or len(report.comparisons) != 48
        or len({check.quantity for check in report.comparisons}) != 48
        or any(check.frequency_hz != FREQUENCY_HZ for check in report.comparisons)
    ):
        raise ValueError(
            "report must qualify all four directional pairs with 48 checks at the exact sample"
        )
    if (
        summary.get("passed") is not report.passed
        or summary.get("report") != report.model_dump(mode="json")
        or summary.get("frequency_hz") != FREQUENCY_HZ
    ):
        raise ValueError("directional benchmark summary and validation report disagree")
    worker_refs = _worker_artifacts(store, summary)
    if len(report.inputs) != len(worker_refs) + 1 or set(report.inputs) != {
        mapping.problem,
        *worker_refs,
    }:
        raise ValueError(
            "directional report must pin the original problem and every worker artifact"
        )
    transfer = _qualified_transfer(summary, responses) if report.passed else None
    operation = OperationInfo(
        name="waveguide.directional_benchmark",
        version=_PROCEDURE_VERSION,
        parameters_digest=content_hash(summary),
    )
    inputs = (
        InputLink(role="seed_problem", target=mapping.problem),
        InputLink(role="template", target=revision_of(template)),
        InputLink(role="template_mapping", target=revision_of(mapping)),
        *(InputLink(role="simulation", target=ref) for ref in worker_refs),
    )
    with store.attempt(operation, inputs) as attempt:
        writer = attempt.new_artifact(_ARTIFACT_KIND)
        writer.write_json("report.json", summary)
        if transfer is not None:
            writer.write_json("transfer.json", transfer.model_dump(mode="json"))
        raw_ref = writer.commit()
        writer = attempt.new_artifact("validation_report")
        writer.write_json("report.json", report.model_dump(mode="json"))
        validation_ref = writer.commit()
    if transfer is None:
        return PublishedDirectionalWaveguide(raw_ref, validation_ref, None, None, None)

    model = template.model_copy(
        update={
            "payload": EvaluatorPayload(
                name=EVALUATOR_KEY[0], version=EVALUATOR_KEY[1]
            ),
            "capability": _CAPABILITY,
            "applicability_spec": _sample_applicability(),
            "evidence": (
                *(
                    EvidenceRef(kind=EvidenceKind.SIMULATION, ref=ref)
                    for ref in worker_refs
                ),
                EvidenceRef(kind=EvidenceKind.SIMULATION, ref=raw_ref),
                EvidenceRef(kind=EvidenceKind.VALIDATION, ref=validation_ref),
            ),
            "metadata": {
                **template.metadata,
                "extraction": _PROCEDURE,
                "polarization": "tm",
                "port_basis": "power_wave",
                "assumptions": "2D effective-index reduction at 1550 nm TM with uniform modal access leads",
            },
        }
    )
    selected_mapping = mapping.model_copy(
        update={
            "model": revision_of(model),
            "responses": tuple(
                ResponseChannel(
                    output=out, excitation=inc, status=ResponseStatus.COMPUTED
                )
                for out, inc in _PAIRS
            ),
        }
    )
    with store.attempt(
        operation,
        (
            InputLink(role="simulation", target=raw_ref),
            InputLink(role="validation", target=validation_ref),
        ),
    ) as attempt:
        writer = attempt.new_artifact("model_revision")
        writer.write_json("model.json", model.model_dump(mode="json"))
        model_ref = writer.commit()
    return PublishedDirectionalWaveguide(
        raw_ref, validation_ref, model_ref, model, selected_mapping
    )


def sample_evaluator(store: ArtifactStore, model: ModelRevision) -> OpticalEvaluator:
    """Restore the exact sample from this model revision's pinned transfer artifact."""
    refs = [
        item.ref
        for item in model.evidence
        if item.kind is EvidenceKind.SIMULATION
        and isinstance(item.ref, ArtifactRef)
        and item.ref.kind == _ARTIFACT_KIND
    ]
    try:
        if (
            len(refs) != 1
            or not isinstance(model.payload, EvaluatorPayload)
            or (model.payload.name, model.payload.version) != EVALUATOR_KEY
            or model.capability != _CAPABILITY
            or model.applicability_spec != _sample_applicability()
            or model.metadata.get("polarization") != "tm"
            or not _model_accepts_sample(model)
        ):
            raise ValueError(
                "model needs one directional transfer artifact and exact-sample TM applicability"
            )
        transfer = _Transfer.model_validate_json(
            store.read_bytes(refs[0], "transfer.json")
        )
        if (
            transfer.schema_version != 1
            or transfer.wavelength_pm != WAVELENGTH_PM
            or transfer.frequency_hz != FREQUENCY_HZ
            or tuple((item.output, item.excitation) for item in transfer.observed)
            != _PAIRS
        ):
            raise ValueError(
                "directional evidence must use schema v1 and cover all four pairs at the exact sample"
            )
    except ValueError as exc:
        raise ArtifactError(
            Diagnostic(
                severity=Severity.ERROR,
                code="waveguide.directional_evidence_invalid",
                message=str(exc),
            )
        ) from exc
    samples = {
        (item.output, item.excitation): complex(item.real, item.imag)
        for item in transfer.observed
    }
    pinned = revision_of(model)

    def evaluate(
        selected: ModelRevision,
        operating: OperatingValues,
        requested: frozenset[ChannelPair],
    ) -> Mapping[ChannelPair, complex]:
        if revision_of(selected) != pinned:
            raise ValueError("sample evaluator used with a different model revision")
        frequency = operating.values.get("frequency")
        if (
            frequency is None
            or frequency.dimension is not Dimension.FREQUENCY
            or frequency.si != FREQUENCY_HZ
            or not requested <= samples.keys()
        ):
            return {}
        return {pair: samples[pair] for pair in requested}

    return evaluate
