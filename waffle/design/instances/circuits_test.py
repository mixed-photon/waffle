from __future__ import annotations

from waffle.contracts.component.design import (
    Circuit,
    CircuitInstance,
    Connection,
    Endpoint,
    Placement,
)
from waffle.contracts.component.examples import PhaseShifterExample, phase_shifter, um
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import Record, load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.design.instances.circuits import build_circuit, validate_circuit


def store(*examples: PhaseShifterExample) -> dict[RevisionRef, Record]:
    records: dict[RevisionRef, Record] = {}
    for ex in examples:
        for record in (ex.interface, ex.component, ex.implementation, ex.design):
            records[revision_of(record)] = record
    return records


def connect(a: str, a_terminal: str, b: str, b_terminal: str) -> Connection:
    return Connection(
        a=Endpoint(instance=a, terminal=a_terminal), b=Endpoint(instance=b, terminal=b_terminal)
    )


def codes(
    circuit: Circuit, records: dict[RevisionRef, Record]
) -> list[tuple[str, tuple[str | int, ...]]]:
    return [(d.code, d.location) for d in validate_circuit(circuit, records.get)]


def circuit(instances: list[CircuitInstance], connections: list[Connection]) -> Circuit:
    return Circuit(
        id=EntityId("circuit", "c"), instances=tuple(instances), connections=tuple(connections)
    )


def test_multiple_instances_share_one_resolved_design() -> None:
    ex = phase_shifter()
    result = build_circuit(
        id=EntityId("circuit", "chain"),
        instances=[
            CircuitInstance(name="ps1", design=ex.design_ref),
            CircuitInstance(
                name="ps2",
                design=ex.design_ref,
                placement=Placement(origin=(um(150), um(0)), rotation_deg=90.0, mirror=True),
            ),
        ],
        connections=[connect("ps1", "out", "ps2", "in")],
        lookup=store(ex).get,
    )
    built = result.unwrap()
    assert built.instances[0].design == built.instances[1].design == ex.design_ref
    reloaded = load_record(to_canonical_json(built), Circuit).unwrap()
    assert reloaded == built
    assert revision_of(reloaded) == revision_of(built)


def test_instances_of_different_designs_coexist() -> None:
    short, long = phase_shifter(50.0), phase_shifter(200.0)
    c = circuit(
        [
            CircuitInstance(name="a", design=short.design_ref),
            CircuitInstance(name="b", design=long.design_ref),
        ],
        [connect("a", "out", "b", "in")],
    )
    assert codes(c, store(short, long)) == []


def test_unknown_instance_and_terminal() -> None:
    ex = phase_shifter()
    c = circuit(
        [CircuitInstance(name="a", design=ex.design_ref)],
        [connect("a", "nope", "ghost", "in")],
    )
    assert codes(c, store(ex)) == [
        ("circuit.unknown_terminal", ("connections", 0, "a")),
        ("circuit.unknown_instance", ("connections", 0, "b")),
    ]


def test_duplicate_instance_names() -> None:
    ex = phase_shifter()
    c = circuit(
        [
            CircuitInstance(name="a", design=ex.design_ref),
            CircuitInstance(name="a", design=ex.design_ref),
        ],
        [],
    )
    assert codes(c, store(ex)) == [("circuit.duplicate_instance", ("instances", 1))]


def test_domain_mismatch() -> None:
    ex = phase_shifter()
    c = circuit(
        [
            CircuitInstance(name="a", design=ex.design_ref),
            CircuitInstance(name="b", design=ex.design_ref),
        ],
        [connect("a", "out", "b", "heater")],
    )
    assert codes(c, store(ex)) == [("circuit.domain_mismatch", ("connections", 0))]


def test_unresolved_reference_in_the_chain() -> None:
    ex = phase_shifter()
    records = store(ex)
    del records[revision_of(ex.component)]
    c = circuit([CircuitInstance(name="a", design=ex.design_ref)], [])
    assert codes(c, records) == [("circuit.unresolved_reference", ("instances", 0, "design"))]
    assert codes(c, {}) == [("circuit.unresolved_reference", ("instances", 0, "design"))]


def test_placement_units_are_checked() -> None:
    ex = phase_shifter()
    placement = Placement(origin=(um(0), Quantity(value=1.0, unit="ps")))
    c = circuit([CircuitInstance(name="a", design=ex.design_ref, placement=placement)], [])
    assert codes(c, store(ex)) == [
        ("units.dimension_mismatch", ("instances", 0, "placement", "origin", 1))
    ]


def test_build_circuit_returns_diagnostics_instead_of_a_circuit() -> None:
    ex = phase_shifter()
    result = build_circuit(
        id=EntityId("circuit", "bad"),
        instances=[CircuitInstance(name="a", design=ex.design_ref)],
        connections=[connect("a", "in", "b", "in")],
        lookup=store(ex).get,
    )
    assert not result.ok
    assert [d.code for d in result.diagnostics] == ["circuit.unknown_instance"]
