from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.experiments.binding import ExperimentBinding, InstanceBinding
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import load_record, to_canonical_json
from waffle.contracts.models.capability import Analysis


def _ref(kind: str) -> RevisionRef:
    return RevisionRef(EntityId(kind, "x"), "sha256:" + "a" * 64)


def test_binding_round_trip_and_payload_xor() -> None:
    entry = InstanceBinding(
        instance="left",
        design=_ref("design"),
        implementation=_ref("implementation"),
        component=_ref("component"),
        interface=_ref("interface"),
        model=_ref("model"),
        mapping=_ref("view_mapping"),
        analysis=Analysis.OPTICAL_SCATTERING,
        operating=OperatingValues(),
        evaluator_name="stub",
        evaluator_version="1",
    )
    binding = ExperimentBinding(
        id=EntityId("experiment_binding", "x"),
        circuit=_ref("circuit"),
        instances=(entry,),
    )
    assert (
        load_record(to_canonical_json(binding), ExperimentBinding).unwrap() == binding
    )
    with pytest.raises(ValidationError):
        InstanceBinding.model_validate(
            {**entry.model_dump(mode="json"), "evaluator_version": None}
        )
