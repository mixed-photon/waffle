from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.component.definition import (
    ComponentDefinition,
    Domain,
    InterfaceDefinition,
    LayoutArtifactSource,
    PhysicalImplementation,
    TechnologyRequirement,
    Terminal,
)
from waffle.contracts.component.design import (
    Circuit,
    CircuitInstance,
    Connection,
    Endpoint,
    ResolvedDesign,
    SampleIdentity,
    Specimen,
)
from waffle.contracts.component.examples import TECHNOLOGY, phase_shifter
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.foundation.records import Record, load_record, revision_of, to_canonical_json


def round_trip[R: Record](record: R) -> R:
    return load_record(to_canonical_json(record), type(record)).unwrap()


def test_every_record_kind_round_trips_with_the_same_revision() -> None:
    ex = phase_shifter()
    circuit = Circuit(
        id=EntityId("circuit", "c"),
        instances=(CircuitInstance(name="a", design=ex.design_ref),),
        connections=(
            Connection(
                a=Endpoint(instance="a", terminal="out"), b=Endpoint(instance="a", terminal="in")
            ),
        ),
    )
    specimen = Specimen(
        id=EntityId("specimen", "s1"),
        design=ex.design_ref,
        sample=SampleIdentity(lot="L1", wafer="W3"),
        fabrication={"run": "2026-10"},
    )
    for record in (ex.interface, ex.component, ex.implementation, ex.design, circuit, specimen):
        loaded = round_trip(record)
        assert loaded == record
        assert revision_of(loaded) == revision_of(record)


def test_exact_dependency_revisions_survive_serialization() -> None:
    ex = phase_shifter()
    loaded = round_trip(ex.design)
    assert loaded.implementation == revision_of(ex.implementation)
    assert loaded.technology == TECHNOLOGY
    assert str(loaded.technology) == f"technology:demo_si@{TECHNOLOGY.digest}"
    assert ex.component.interface == revision_of(ex.interface)
    assert f'"technology":"{TECHNOLOGY}"' in to_canonical_json(ex.design)


def test_multiple_specimens_reference_one_fabricated_revision() -> None:
    ex = phase_shifter()
    specimens = [
        Specimen(
            id=EntityId("specimen", f"die_{n}"),
            design=ex.design_ref,
            sample=SampleIdentity(lot="L1", wafer="W3", die=str(n)),
        )
        for n in range(3)
    ]
    reloaded = [round_trip(s) for s in specimens]
    assert {s.design for s in reloaded} == {ex.design_ref}
    assert len({revision_of(s) for s in reloaded}) == 3


def test_dependency_revision_changes_when_a_dependency_changes() -> None:
    a, b = phase_shifter(100.0), phase_shifter(120.0)
    assert a.design_ref != b.design_ref
    other = ex_with_other_technology(a.design)
    assert revision_of(other) != revision_of(a.design)


def ex_with_other_technology(design: ResolvedDesign) -> ResolvedDesign:
    return design.model_copy(
        update={"technology": RevisionRef(TECHNOLOGY.entity, "sha256:" + "2" * 64)}
    )


def test_references_must_point_at_the_right_kind() -> None:
    ex = phase_shifter()
    with pytest.raises(ValidationError, match="expected a 'component'"):
        PhysicalImplementation(
            id=EntityId("implementation", "bad"),
            component=revision_of(ex.interface),
            source=ex.implementation.source,
            technology=ex.implementation.technology,
        )
    with pytest.raises(ValidationError, match="not a technology"):
        TechnologyRequirement(technology=EntityId("component", "x"))


def test_layout_artifact_source_round_trips() -> None:
    ex = phase_shifter()
    artifact = ArtifactRef("layout", "sha256:" + "3" * 64)
    impl = ex.implementation.model_copy(update={"source": LayoutArtifactSource(artifact=artifact)})
    loaded = round_trip(impl)
    assert loaded.source == LayoutArtifactSource(artifact=artifact)


def test_operating_conditions_do_not_belong_on_a_resolved_design() -> None:
    payload = phase_shifter().design.model_dump(mode="json")
    payload["operating_conditions"] = {"role": "operating", "values": {}}
    assert not load_record(payload, ResolvedDesign).ok


def test_interface_terminal_names_are_unique_and_non_empty() -> None:
    t = Terminal(name="a", domain=Domain.OPTICAL)
    with pytest.raises(ValidationError, match="unique"):
        InterfaceDefinition(id=EntityId("interface", "i"), terminals=(t, t))
    with pytest.raises(ValidationError):
        InterfaceDefinition(id=EntityId("interface", "i"), terminals=())


def test_descriptive_metadata_is_part_of_the_revision() -> None:
    ex = phase_shifter()
    renamed = ex.component.model_copy(update={"metadata": {"title": "Other"}})
    assert revision_of(renamed) != revision_of(ex.component)
    assert isinstance(ex.component, ComponentDefinition)


def test_identifiers_are_fully_matched() -> None:
    with pytest.raises(ValidationError):
        Endpoint(instance="a/b", terminal="o1")
    with pytest.raises(ValidationError):
        Terminal(name="o-1", domain=Domain.OPTICAL)
