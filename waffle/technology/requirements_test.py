from __future__ import annotations

from waffle.contracts.component.definition import TechnologyRequirement
from waffle.contracts.foundation.identity import EntityId
from waffle.pdk.testing.synthetic import TECHNOLOGY_ID, synthetic_soi
from waffle.technology.requirements import check_requirement


def test_satisfied_requirement_has_no_findings() -> None:
    requirement = TechnologyRequirement(technology=TECHNOLOGY_ID, layers=("WG", "SLAB", "PORT"))
    assert check_requirement(requirement, synthetic_soi()) == []


def test_wrong_technology_is_reported() -> None:
    requirement = TechnologyRequirement(technology=EntityId("technology", "other"), layers=("WG",))
    found = check_requirement(requirement, synthetic_soi())
    assert [d.code for d in found] == ["technology.requirement_id_mismatch"]
    assert found[0].location == ("technology",)


def test_each_missing_layer_is_reported_at_its_index() -> None:
    requirement = TechnologyRequirement(technology=TECHNOLOGY_ID, layers=("WG", "HEATER", "VIA"))
    found = check_requirement(requirement, synthetic_soi())
    assert [(d.code, d.location) for d in found] == [
        ("technology.requirement_unknown_layer", ("layers", 1)),
        ("technology.requirement_unknown_layer", ("layers", 2)),
    ]
    assert "'HEATER'" in found[0].message
