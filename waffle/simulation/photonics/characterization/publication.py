"""Publish bounded transfer evidence and explicitly qualified exact-sample models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.characterization.report import ValidationReport
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
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
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.models.evaluate import ChannelPair, OpticalEvaluator
from waffle.simulation.photonics.characterization.waveguide import WaveguideEvidence

EVALUATOR_KEY = ("waffle.waveguide.samples", "1")


@dataclass(frozen=True)
class PublishedWaveguide:
    evidence: ArtifactRef
    validation: ArtifactRef
    model_artifact: ArtifactRef | None
    model: ModelRevision | None


def publish_waveguide(
    store: ArtifactStore,
    template: ModelRevision,
    evidence: WaveguideEvidence,
    report: ValidationReport,
    *,
    modal_qualification: str | None = None,
    inputs: tuple[InputLink, ...] = (),
) -> PublishedWaveguide:
    """Publish raw evidence always; modal model requires passed, explained qualification.

    Qualification must identify the evidence supporting single forward mode,
    equal cross-section, reference planes, and negligible launch/end reflection.
    Raw Gaussian-source field ratios by themselves do not meet this requirement.
    """
    pair = (evidence.output_channel, evidence.excitation_channel)
    if report.observed_channels != (pair,):
        raise ValueError("report must identify exactly the observed transfer")
    required = {
        (sample.frequency_hz, kind, quantity)
        for sample in evidence.samples
        for kind in ("analytic_reference", "grid_convergence")
        for quantity in ("magnitude", "wrapped_phase")
    }
    available = {
        (item.frequency_hz, item.comparison, item.quantity)
        for item in report.comparisons
    }
    if not required <= available:
        raise ValueError(
            "report must compare magnitude and phase against reference and grid at every sample"
        )
    if modal_qualification is not None and (
        not report.passed or not modal_qualification.strip()
    ):
        raise ValueError(
            "modal publication needs a passing report and qualification rationale"
        )
    parameters = {
        "evidence": evidence.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
        "qualification": modal_qualification,
    }
    operation = OperationInfo(
        name="waveguide.characterize",
        version="1",
        parameters_digest=content_hash(parameters),
    )
    with store.attempt(
        operation, (*inputs, InputLink(role="template", target=revision_of(template)))
    ) as session:
        raw = session.new_artifact("waveguide_transfer")
        raw.write_json("transfer.json", evidence.model_dump(mode="json"))
        raw_ref = raw.commit()
        validation = session.new_artifact("validation_report")
        validation.write_json("report.json", report.model_dump(mode="json"))
        validation_ref = validation.commit()
    if modal_qualification is None:
        return PublishedWaveguide(raw_ref, validation_ref, None, None)
    model = template.model_copy(
        update={
            "payload": EvaluatorPayload(
                name=EVALUATOR_KEY[0], version=EVALUATOR_KEY[1]
            ),
            "capability": OpticalScattering(observed_channels=(pair,)),
            "applicability_spec": Applicability(
                axes=(
                    AxisCoverage(
                        parameter="frequency",
                        sampled=tuple(
                            Quantity(value=sample.frequency_hz, unit="Hz")
                            for sample in evidence.samples
                        ),
                    ),
                )
            ),
            "evidence": (
                *(
                    EvidenceRef(kind=EvidenceKind.SIMULATION, ref=link.target)
                    for link in inputs
                    if isinstance(link.target, ArtifactRef)
                    and link.target.kind == "simulation_fields"
                ),
                *(
                    item
                    for item in template.evidence
                    if not (
                        isinstance(item.ref, ArtifactRef)
                        and item.ref.kind == "waveguide_transfer"
                    )
                ),
                EvidenceRef(kind=EvidenceKind.SIMULATION, ref=raw_ref),
                EvidenceRef(kind=EvidenceKind.VALIDATION, ref=validation_ref),
            ),
            "metadata": {
                **template.metadata,
                "modal_qualification": modal_qualification,
                "extraction": evidence.extraction,
            },
        }
    )
    with store.attempt(
        operation,
        (
            InputLink(role="simulation", target=raw_ref),
            InputLink(role="validation", target=validation_ref),
        ),
    ) as session:
        writer = session.new_artifact("model_revision")
        writer.write_json("model.json", model.model_dump(mode="json"))
        model_ref = writer.commit()
    return PublishedWaveguide(raw_ref, validation_ref, model_ref, model)


def sample_evaluator(store: ArtifactStore, model: ModelRevision) -> OpticalEvaluator:
    """Restore the pinned exact-sample evaluator from immutable model evidence."""
    refs = [
        item.ref
        for item in model.evidence
        if item.kind is EvidenceKind.SIMULATION
        and isinstance(item.ref, ArtifactRef)
        and item.ref.kind == "waveguide_transfer"
    ]
    if (
        len(refs) != 1
        or not isinstance(model.payload, EvaluatorPayload)
        or (model.payload.name, model.payload.version) != EVALUATOR_KEY
    ):
        raise ArtifactError(
            Diagnostic(
                severity=Severity.ERROR,
                code="waveguide.evidence_unavailable",
                message="model needs one waveguide transfer artifact and supported evaluator",
            )
        )
    try:
        evidence = WaveguideEvidence.model_validate_json(
            store.read_bytes(refs[0], "transfer.json")
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            Diagnostic(
                severity=Severity.ERROR,
                code="waveguide.evidence_invalid",
                message=str(exc),
            )
        ) from exc
    samples = {sample.frequency_hz: sample.amplitude for sample in evidence.samples}
    pair = (evidence.output_channel, evidence.excitation_channel)
    pinned = revision_of(model)

    def evaluate(
        selected: ModelRevision,
        operating: OperatingValues,
        requested: frozenset[ChannelPair],
    ) -> Mapping[ChannelPair, complex]:
        if revision_of(selected) != pinned:
            raise ValueError("sample evaluator used with a different model revision")
        frequency = operating.values["frequency"].si
        if frequency not in samples or requested != frozenset((pair,)):
            return {}
        return {pair: samples[frequency]}

    return evaluate
