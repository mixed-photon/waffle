from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from waffle.artifacts.local import LocalArtifactStore
from waffle.artifacts.store import ArtifactError
from waffle.contracts.characterization.report import Comparison, ValidationReport
from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.model import (
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.results.provenance import OperationInfo
from waffle.models.evaluate import ChannelPair, evaluate_optical_scattering
from waffle.simulation.photonics.characterization.directional_publication import (
    EVALUATOR_KEY,
    FREQUENCY_HZ,
    PublishedDirectionalWaveguide,
    publish_directional_waveguide,
    sample_evaluator,
)

_PORTS = ("o1", "o2")
_PAIRS = tuple((out, inc) for out in _PORTS for inc in _PORTS)
_RESPONSES: dict[ChannelPair, complex] = dict(
    zip(_PAIRS, (0.0001j, 1.00005j, 1.0001j, 0.0002 + 0.0001j), strict=True)
)


@dataclass(frozen=True)
class _Fixture:
    store: LocalArtifactStore
    interface: InterfaceDefinition
    template: ModelRevision
    mapping: ViewMapping
    report: ValidationReport
    summary: dict[str, object]


@pytest.fixture
def fixture(tmp_path: Path) -> _Fixture:
    interface = InterfaceDefinition(
        id="interface:directional",
        terminals=tuple(Terminal(name=p, domain=Domain.OPTICAL) for p in _PORTS),
    )
    template = ModelRevision(
        id="model:directional",
        interface=revision_of(interface),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        payload=EvaluatorPayload(name="synthetic", version="1"),
    )
    digest = "sha256:" + "a" * 64
    mapping = ViewMapping(
        id="view_mapping:directional",
        interface=revision_of(interface),
        layout=f"layout:directional@{digest}",
        problem=f"physical_problem:directional@{digest}",
        model=revision_of(template),
        bindings=tuple(
            ChannelBinding(
                terminal=port,
                layout_port=port,
                anchor=PortAnchor(
                    center=(2000 * index, 0), orientation=180 if index == 0 else 0
                ),
                simulation_port=port,
                model_channel=port,
            )
            for index, port in enumerate(_PORTS)
        ),
    )
    store = LocalArtifactStore(tmp_path)
    pins = []
    runs = {}
    for setting in ("fine", "medium", "coarse", "duration", "boundary"):
        runs[setting] = []
        for launch in _PORTS:
            parameters = {"setting": setting, "launch": launch}
            with store.attempt(
                OperationInfo(
                    name="fixture.fields",
                    version="1",
                    parameters_digest=content_hash(parameters),
                )
            ) as attempt:
                writer = attempt.new_artifact("simulation_fields")
                writer.write_json("fields.json", parameters)
                artifact = writer.commit()
            pins.append(artifact)
            runs[setting].append(
                {"artifact": str(artifact), "attempt_id": attempt.attempt_id}
            )
    # Synthetic discrepancies exercise publication and are independent of the physical benchmark.
    report = ValidationReport(
        id="validation_report:directional",
        procedure="uniform_guide_directional_two_port",
        procedure_version="1",
        reference_description="Synthetic directional publication fixture",
        inputs=(mapping.problem, *pins),
        observed_channels=_PAIRS,
        comparisons=tuple(
            Comparison(
                quantity=f"quantity_{index}",
                frequency_hz=FREQUENCY_HZ,
                error=0.001,
                tolerance=0.005,
                unit="1",
                comparison="analytic_reference",
            )
            for index in range(48)
        ),
        limitations=("Only one exact sample",),
    )
    summary = {
        "passed": report.passed,
        "frequency_hz": FREQUENCY_HZ,
        "runs": runs,
        "estimates": {
            "fine": {
                "matrix": [
                    [
                        [_RESPONSES[out, inc].real, _RESPONSES[out, inc].imag]
                        for inc in _PORTS
                    ]
                    for out in _PORTS
                ],
            },
        },
        "report": report.model_dump(mode="json"),
    }
    return _Fixture(store, interface, template, mapping, report, summary)


def _publish(fixture: _Fixture) -> PublishedDirectionalWaveguide:
    return publish_directional_waveguide(
        fixture.store,
        fixture.template,
        fixture.mapping,
        fixture.report,
        fixture.summary,
        responses=_RESPONSES,
    )


def test_published_model_replays_raw_matrix_and_subsets(fixture: _Fixture) -> None:
    published = _publish(fixture)
    store = fixture.store
    assert published.model_artifact is not None
    assert published.mapping is not None
    model = ModelRevision.model_validate_json(
        store.read_bytes(published.model_artifact, "model.json")
    )
    assert model == published.model
    assert published.mapping.model == revision_of(model)
    assert published.mapping.bindings == fixture.mapping.bindings
    assert (
        tuple((entry.output, entry.excitation) for entry in published.mapping.responses)
        == _PAIRS
    )
    assert all(
        entry.status is ResponseStatus.COMPUTED for entry in published.mapping.responses
    )
    assert (
        json.loads(store.read_bytes(published.evidence, "report.json"))
        == fixture.summary
    )
    assert (
        ValidationReport.model_validate_json(
            store.read_bytes(published.validation, "report.json")
        )
        == fixture.report
    )
    assert [
        link.target
        for link in store.provenance_of(published.evidence)[0].inputs
        if link.role == "simulation"
    ] == list(fixture.report.inputs[1:])
    assert {
        link.target for link in store.provenance_of(published.model_artifact)[0].inputs
    } == {published.evidence, published.validation}
    evaluator = sample_evaluator(store, model)
    for pairs in (_PAIRS, (_PAIRS[1], _PAIRS[3])):
        result = evaluate_optical_scattering(
            model,
            fixture.interface,
            OperatingValues(
                values={"frequency": Quantity(value=FREQUENCY_HZ, unit="Hz")}
            ),
            published.mapping,
            pairs,
            {EVALUATOR_KEY: evaluator},
        )
        assert result.unwrap() == {pair: _RESPONSES[pair] for pair in pairs}
    off_sample = evaluate_optical_scattering(
        model,
        fixture.interface,
        OperatingValues(
            values={
                "frequency": Quantity(
                    value=math.nextafter(FREQUENCY_HZ, math.inf), unit="Hz"
                )
            }
        ),
        published.mapping,
        _PAIRS,
        {EVALUATOR_KEY: evaluator},
    )
    assert not off_sample.ok
    assert "model.extrapolation" in {item.code for item in off_sample.diagnostics}


def test_failed_tolerance_retains_report_and_raw_estimate(fixture: _Fixture) -> None:
    first, *others = fixture.report.comparisons
    report = fixture.report.model_copy(
        update={"comparisons": (first.model_copy(update={"error": 1.0}), *others)}
    )
    summary = {
        **fixture.summary,
        "passed": False,
        "report": report.model_dump(mode="json"),
    }
    published = publish_directional_waveguide(
        fixture.store,
        fixture.template,
        fixture.mapping,
        report,
        summary,
        responses=_RESPONSES,
    )
    assert published.model is published.model_artifact is published.mapping is None
    assert (
        json.loads(fixture.store.read_bytes(published.evidence, "report.json"))
        == summary
    )
    assert (
        ValidationReport.model_validate_json(
            fixture.store.read_bytes(published.validation, "report.json")
        )
        == report
    )


def test_rejects_inconsistent_pins_and_responses(fixture: _Fixture) -> None:
    report = fixture.report.model_copy(update={"inputs": fixture.report.inputs[:-1]})
    with pytest.raises(
        ValueError, match="must pin the original problem and every worker artifact"
    ):
        publish_directional_waveguide(
            fixture.store,
            fixture.template,
            fixture.mapping,
            report,
            {**fixture.summary, "report": report.model_dump(mode="json")},
            responses=_RESPONSES,
        )
    with pytest.raises(ValueError, match="unmodified fine estimate"):
        publish_directional_waveguide(
            fixture.store,
            fixture.template,
            fixture.mapping,
            fixture.report,
            fixture.summary,
            responses={**_RESPONSES, ("o1", "o2"): 1j},
        )
    with pytest.raises(ValueError, match="four finite complex responses"):
        publish_directional_waveguide(
            fixture.store,
            fixture.template,
            fixture.mapping,
            fixture.report,
            fixture.summary,
            responses={("o1", "o2"): 1j},
        )


@pytest.mark.parametrize(
    "corruption", ("schema", "schema_type", "duplicate", "nonfinite")
)
def test_replay_rejects_malformed_transfer(fixture: _Fixture, corruption: str) -> None:
    published = _publish(fixture)
    assert published.model is not None
    transfer = json.loads(fixture.store.read_bytes(published.evidence, "transfer.json"))
    if corruption == "schema":
        transfer["schema_version"] = 2
    elif corruption == "schema_type":
        transfer["schema_version"] = True
    elif corruption == "duplicate":
        transfer["observed"][-1] = transfer["observed"][0]
    else:
        transfer["observed"][0]["real"] = math.inf
    with fixture.store.attempt(
        OperationInfo(
            name="fixture.corruption",
            version="1",
            parameters_digest=content_hash(corruption),
        )
    ) as attempt:
        writer = attempt.new_artifact(published.evidence.kind)
        writer.write_bytes(
            "transfer.json", json.dumps(transfer).encode(), "application/json"
        )
        ref = writer.commit()
    model = published.model.model_copy(
        update={"evidence": (EvidenceRef(kind=EvidenceKind.SIMULATION, ref=ref),)}
    )
    with pytest.raises(ArtifactError, match="waveguide.directional_evidence_invalid"):
        sample_evaluator(fixture.store, model)
