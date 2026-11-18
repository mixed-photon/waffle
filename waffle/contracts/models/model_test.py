from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.component.examples import phase_shifter
from waffle.contracts.component.parameters import (
    DesignValues,
    FittedCoefficients,
    OperatingConditionSchema,
    ParameterSpec,
)
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import (
    load_record,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.models.capability import ElectricalDescriptor, OpticalScattering
from waffle.contracts.models.model import (
    ArtifactPayload,
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    Extension,
    ModelRevision,
)
from waffle.contracts.results.artifact import ArtifactRef


def model(**kwargs: object) -> ModelRevision:
    ex = phase_shifter()
    fields: dict[str, object] = {
        "id": EntityId("model", "ps_analytic"),
        "interface": revision_of(ex.interface),
        "implementations": (revision_of(ex.implementation),),
        "designs": (ex.design_ref,),
        "operating_conditions": OperatingConditionSchema(
            parameters=(ParameterSpec(name="wavelength", dimension=Dimension.LENGTH),)
        ),
        "coefficients": FittedCoefficients(
            values={"loss": Quantity(value=0.3, unit="1")}
        ),
        "payload": EvaluatorPayload(name="ps_analytic", version="0.1"),
        "evidence": (EvidenceRef(kind=EvidenceKind.SIMULATION, ref=ex.design_ref),),
        "capabilities": (
            Extension(name="optical.sparameters", version=1, body={"bands": [1]}),
        ),
        "applicability": (Extension(name="optical.range", version=1),),
    }
    fields.update(kwargs)
    return ModelRevision(**fields)  # type: ignore[arg-type]


def test_model_round_trips_with_exact_dependency_revisions() -> None:
    original = model()
    loaded = load_record(to_canonical_json(original), ModelRevision).unwrap()
    assert loaded == original
    assert revision_of(loaded) == revision_of(original)
    ex = phase_shifter()
    assert loaded.interface == revision_of(ex.interface)
    assert loaded.designs == (ex.design_ref,)
    assert loaded.evidence[0].ref == ex.design_ref


def test_coefficient_changes_make_a_new_revision() -> None:
    other = model(
        coefficients=FittedCoefficients(values={"loss": Quantity(value=0.4, unit="1")})
    )
    assert revision_of(other) != revision_of(model())


def test_operating_conditions_and_coefficients_are_separate_roles() -> None:
    with pytest.raises(ValidationError):
        model(operating_conditions=DesignValues())
    with pytest.raises(ValidationError):
        model(coefficients=OperatingConditionSchema())


def test_references_are_kind_checked() -> None:
    with pytest.raises(ValidationError, match="expected a 'interface'"):
        model(interface=phase_shifter().design_ref)


def test_payload_variants() -> None:
    stored = ArtifactPayload(artifact=ArtifactRef("model", "sha256:" + "a" * 64))
    assert (
        load_record(to_canonical_json(model(payload=stored)), ModelRevision)
        .unwrap()
        .payload
        == stored
    )
    with pytest.raises(ValidationError):
        ArtifactPayload(artifact="model@md5:abc")


def test_evidence_may_be_an_artifact_or_a_record_revision() -> None:
    dataset = ArtifactRef("dataset", "sha256:" + "b" * 64)
    evidence = (
        EvidenceRef(kind=EvidenceKind.MEASUREMENT, ref=dataset),
        EvidenceRef(kind=EvidenceKind.SIMULATION, ref=phase_shifter().design_ref),
    )
    loaded = load_record(
        to_canonical_json(model(evidence=evidence)), ModelRevision
    ).unwrap()
    assert loaded.evidence == evidence


def test_unknown_coefficient_units_are_reported() -> None:
    assert model().check_units() == []
    bad = model(
        coefficients=FittedCoefficients(
            values={"k": Quantity(value=1.0, unit="furlong")}
        )
    )
    [d] = bad.check_units()
    assert d.code == "units.unknown_unit"
    assert d.location == ("coefficients", "values", "k")


def test_v1_extension_record_remains_readable() -> None:
    legacy = model().model_dump(mode="json")
    legacy["schema_version"] = 1
    del legacy["capability"]
    del legacy["applicability_spec"]
    loaded = load_record(legacy, ModelRevision).unwrap()
    assert loaded.schema_version == 2
    assert loaded.capabilities == model().capabilities
    assert loaded.capability is None


def test_electrical_descriptor_shares_revision_metadata() -> None:
    optical = model(capability=OpticalScattering())
    electrical = model(capability=ElectricalDescriptor())
    assert electrical.interface == optical.interface
    assert electrical.payload == optical.payload
    assert electrical.evidence == optical.evidence
    assert revision_of(electrical) != revision_of(optical)
