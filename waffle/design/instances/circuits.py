"""Build and validate circuits of instances."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from waffle.contracts.component.definition import (
    ComponentDefinition,
    InterfaceDefinition,
    PhysicalImplementation,
)
from waffle.contracts.component.design import Circuit, CircuitInstance, Connection, ResolvedDesign
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import Record, RecordLookup
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension


def build_circuit(
    *,
    id: EntityId,
    instances: Iterable[CircuitInstance],
    connections: Iterable[Connection],
    lookup: RecordLookup,
    metadata: Mapping[str, str] | None = None,
) -> Result[Circuit]:
    """Create a circuit, or return the diagnostics of `validate_circuit`."""
    circuit = Circuit(
        id=id,
        instances=tuple(instances),
        connections=tuple(connections),
        metadata=dict(metadata or {}),
    )
    return Result.of(circuit, validate_circuit(circuit, lookup))


def validate_circuit(circuit: Circuit, lookup: RecordLookup) -> list[Diagnostic]:
    """Check names, placements, and connections.

    `lookup` must resolve each instance's design, and through it the implementation,
    component, and interface revisions. Unresolvable references are diagnostics.
    """
    subject = str(circuit.id)

    def error(code: str, message: str, *location: str | int) -> Diagnostic:
        return Diagnostic(
            severity=Severity.ERROR, code=code, message=message, subject=subject, location=location
        )

    found: list[Diagnostic] = []
    interfaces: dict[str, InterfaceDefinition] = {}
    for i, instance in enumerate(circuit.instances):
        at = ("instances", i)
        if instance.name in interfaces or instance.name in {
            earlier.name for earlier in circuit.instances[:i]
        }:
            found.append(
                error("circuit.duplicate_instance", f"duplicate instance {instance.name!r}", *at)
            )
            continue
        for axis, origin in enumerate(instance.placement.origin):
            found += origin.diagnostics(
                Dimension.LENGTH, subject=subject, location=(*at, "placement", "origin", axis)
            )
        interface = _interface_of(instance.design, lookup)
        if isinstance(interface, str):
            found.append(error("circuit.unresolved_reference", interface, *at, "design"))
        else:
            interfaces[instance.name] = interface

    names = {instance.name for instance in circuit.instances}
    for i, connection in enumerate(circuit.connections):
        domains = []
        for side, endpoint in (("a", connection.a), ("b", connection.b)):
            where = ("connections", i, side)
            if endpoint.instance not in names:
                found.append(
                    error("circuit.unknown_instance", f"no instance {endpoint.instance!r}", *where)
                )
            elif endpoint.instance in interfaces:
                terminal = interfaces[endpoint.instance].terminal(endpoint.terminal)
                if terminal is None:
                    found.append(
                        error("circuit.unknown_terminal", f"{endpoint} is not a terminal", *where)
                    )
                else:
                    domains.append(terminal.domain)
        if len(domains) == 2 and domains[0] is not domains[1]:
            found.append(
                error(
                    "circuit.domain_mismatch",
                    f"cannot connect {domains[0]} to {domains[1]} terminals",
                    "connections",
                    i,
                )
            )
    return found


_CHAIN: tuple[tuple[type[Record], str], ...] = (
    (ResolvedDesign, "implementation"),
    (PhysicalImplementation, "component"),
    (ComponentDefinition, "interface"),
)


def _interface_of(design: RevisionRef, lookup: RecordLookup) -> InterfaceDefinition | str:
    """The interface behind a design revision, or a message saying what is missing."""
    ref = design
    for expected, attribute in _CHAIN:
        record = lookup(ref)
        if not isinstance(record, expected):
            return f"{ref} did not resolve to a {expected.__name__}"
        ref = getattr(record, attribute)
    interface = lookup(ref)
    if not isinstance(interface, InterfaceDefinition):
        return f"{ref} did not resolve to an InterfaceDefinition"
    return interface
