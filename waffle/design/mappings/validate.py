"""Validate pinned view mappings and use them for physical connectivity."""

from __future__ import annotations

from collections.abc import Iterable

from waffle.contracts.component.definition import InterfaceDefinition
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.layout.layout import Layout, Port
from waffle.contracts.mappings.mapping import (
    ModalTransform,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.model import ModelRevision
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.layout.services.flatten import flatten


def _error(
    mapping: ViewMapping, code: str, message: str, *path: str | int
) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR,
        code=code,
        message=message,
        subject=str(mapping.id),
        location=path,
    )


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return duplicated


def validate_mapping(
    mapping: ViewMapping,
    interface: InterfaceDefinition,
    layout: Layout,
    problem: PhysicalProblem,
    model: ModelRevision | None = None,
) -> list[Diagnostic]:
    """Check exact source revisions, a total bijection, geometry, and response scope.

    An anchor is the independent geometric assertion that detects a swapped path even
    when the swapped port has the same signal domain and width.
    """

    found: list[Diagnostic] = []
    for name, expected, actual in (
        ("interface", mapping.interface, revision_of(interface)),
        ("layout", mapping.layout, revision_of(layout)),
        ("problem", mapping.problem, revision_of(problem)),
    ):
        if expected != actual:
            found.append(
                _error(
                    mapping,
                    "mapping.revision_mismatch",
                    f"{name} must be {expected}, got {actual}",
                    name,
                )
            )
    if problem.layout != mapping.layout:
        found.append(
            _error(
                mapping,
                "mapping.source_mismatch",
                "physical problem does not use this layout revision",
                "problem",
            )
        )
    if problem.top_cell != layout.top or problem.dbu_pm != layout.grid.dbu_pm:
        found.append(
            _error(
                mapping,
                "mapping.source_mismatch",
                "physical problem top cell or grid differs from layout",
                "problem",
            )
        )
    if model is not None:
        if mapping.model != revision_of(model):
            found.append(
                _error(
                    mapping,
                    "mapping.revision_mismatch",
                    "model revision does not match the mapping",
                    "model",
                )
            )
        if model.interface != mapping.interface:
            found.append(
                _error(
                    mapping,
                    "mapping.interface_mismatch",
                    "model references a different interface revision",
                    "model",
                )
            )
    elif mapping.model is not None:
        found.append(
            _error(
                mapping,
                "mapping.unresolved_model",
                "model record is required to validate its pinned revision",
                "model",
            )
        )

    terminals = {t.name: t for t in interface.terminals}
    bound = {b.terminal for b in mapping.bindings}
    for name in sorted(terminals.keys() - bound):
        found.append(
            _error(
                mapping,
                "mapping.missing_terminal",
                f"terminal {name!r} is unmapped",
                "bindings",
            )
        )
    for name in sorted(bound - terminals.keys()):
        found.append(
            _error(
                mapping,
                "mapping.unknown_terminal",
                f"terminal {name!r} is absent from the interface",
                "bindings",
            )
        )
    for field in ("terminal", "layout_port", "simulation_port", "model_channel"):
        for value in sorted(_duplicates(getattr(b, field) for b in mapping.bindings)):
            found.append(
                _error(
                    mapping,
                    "mapping.duplicate_binding",
                    f"{field} {value!r} is used more than once",
                    "bindings",
                )
            )
    for value in sorted(
        _duplicates(
            b.measurement_channel
            for b in mapping.bindings
            if b.measurement_channel is not None
        )
    ):
        found.append(
            _error(
                mapping,
                "mapping.duplicate_binding",
                f"measurement_channel {value!r} is used more than once",
                "bindings",
            )
        )

    flat = flatten(layout)
    if flat.value is None:
        found.extend(flat.diagnostics)
        layout_ports: dict[str, Port] = {}
    else:
        layout_ports = {q.path: q.port for q in flat.value.ports}
    physical_ports = {p.path: p for p in problem.description.ports}
    for index, binding in enumerate(mapping.bindings):
        at = ("bindings", index)
        terminal = terminals.get(binding.terminal)
        layout_port = layout_ports.get(binding.layout_port)
        physical_port = physical_ports.get(binding.simulation_port)
        if layout_port is None:
            found.append(
                _error(
                    mapping,
                    "mapping.unknown_layout_port",
                    f"layout port {binding.layout_port!r} is absent",
                    *at,
                    "layout_port",
                )
            )
        if physical_port is None:
            found.append(
                _error(
                    mapping,
                    "mapping.unknown_simulation_port",
                    f"simulation port {binding.simulation_port!r} is absent",
                    *at,
                    "simulation_port",
                )
            )
        if (
            terminal is not None
            and layout_port is not None
            and terminal.domain != layout_port.domain
        ):
            found.append(
                _error(
                    mapping,
                    "mapping.domain_mismatch",
                    f"terminal {binding.terminal!r} and layout port have different domains",
                    *at,
                )
            )
        if (
            terminal is not None
            and physical_port is not None
            and terminal.domain != physical_port.domain
        ):
            found.append(
                _error(
                    mapping,
                    "mapping.domain_mismatch",
                    f"terminal {binding.terminal!r} and simulation port have different domains",
                    *at,
                )
            )
        if layout_port is not None:
            placed = layout_port.transformed(mapping.transform)
            if (placed.center, placed.orientation) != (
                binding.anchor.center,
                binding.anchor.orientation,
            ):
                found.append(
                    _error(
                        mapping,
                        "mapping.port_swap",
                        f"layout port {binding.layout_port!r} misses terminal {binding.terminal!r} anchor",
                        *at,
                        "anchor",
                    )
                )
        if (
            layout_port is not None
            and physical_port is not None
            and (
                layout_port.center != physical_port.center
                or layout_port.orientation != physical_port.orientation
                or layout_port.width != physical_port.width
                or layout_port.layer != physical_port.layer
                or layout_port.domain != physical_port.domain
            )
        ):
            found.append(
                _error(
                    mapping,
                    "mapping.port_mismatch",
                    "simulation port does not represent this layout port",
                    *at,
                    "simulation_port",
                )
            )
        if binding.modal_transform is not ModalTransform.IDENTITY:
            found.append(
                _error(
                    mapping,
                    "mapping.unsupported_modal_transform",
                    f"modal transform {binding.modal_transform.value!r} is unsupported",
                    *at,
                    "modal_transform",
                )
            )
        if binding.mode != 0:
            found.append(
                _error(
                    mapping,
                    "mapping.unsupported_mode",
                    "only fundamental mode 0 is supported",
                    *at,
                    "mode",
                )
            )
    # Validate declared response identities without demanding any computed elements.
    found.extend(validate_response_coverage(mapping, ()))
    return found


def validate_response_coverage(
    mapping: ViewMapping,
    required_pairs: Iterable[tuple[str, str]] | None = None,
) -> list[Diagnostic]:
    """Report absent and explicitly uncomputed output/excitation pairs.

    By default require the full square matrix over mapped model channels. Callers may
    pass a subset for a bounded analysis without claiming the rest was computed.
    """

    channels = {b.model_channel for b in mapping.bindings}
    responses: dict[tuple[str, str], ResponseStatus] = {}
    found: list[Diagnostic] = []
    for index, response in enumerate(mapping.responses):
        pair = (response.output, response.excitation)
        if pair in responses:
            found.append(
                _error(
                    mapping,
                    "mapping.duplicate_response",
                    f"response {pair} is duplicated",
                    "responses",
                    index,
                )
            )
        if pair[0] not in channels or pair[1] not in channels:
            found.append(
                _error(
                    mapping,
                    "mapping.unknown_response_channel",
                    f"response {pair} names an unmapped channel",
                    "responses",
                    index,
                )
            )
        responses[pair] = response.status
    required = (
        set(required_pairs)
        if required_pairs is not None
        else {(out, exc) for out in channels for exc in channels}
    )
    for pair in sorted(required):
        status = responses.get(pair)
        if status is not ResponseStatus.COMPUTED:
            reason = "uncomputed" if status is ResponseStatus.UNCOMPUTED else "absent"
            found.append(
                _error(
                    mapping,
                    "mapping.uncomputed_response",
                    f"response {pair} is {reason}",
                    "responses",
                )
            )
    return found


def check_connectivity(
    left: ViewMapping,
    left_terminal: str,
    left_layout: Layout,
    right: ViewMapping,
    right_terminal: str,
    right_layout: Layout,
) -> list[Diagnostic]:
    """Check mating geometry after each mapping's exact placement transform."""

    found: list[Diagnostic] = []
    for mapping, layout in ((left, left_layout), (right, right_layout)):
        if mapping.layout != revision_of(layout):
            found.append(
                _error(
                    mapping,
                    "mapping.revision_mismatch",
                    "layout revision does not match the mapping",
                    "layout",
                )
            )
    if left_layout.grid.dbu_pm != right_layout.grid.dbu_pm:
        found.append(
            _error(
                left,
                "mapping.connectivity.grid_mismatch",
                "layout grid sizes differ; regrid before checking connectivity",
            )
        )
    if found:
        return found

    def port(mapping: ViewMapping, terminal: str, layout: Layout) -> Port | None:
        binding = next((b for b in mapping.bindings if b.terminal == terminal), None)
        flat = flatten(layout)
        if binding is None or flat.value is None:
            return None
        physical = next(
            (
                q.port.transformed(mapping.transform)
                for q in flat.value.ports
                if q.path == binding.layout_port
            ),
            None,
        )
        if physical is None or (physical.center, physical.orientation) != (
            binding.anchor.center,
            binding.anchor.orientation,
        ):
            return None
        return physical

    a = port(left, left_terminal, left_layout)
    b = port(right, right_terminal, right_layout)
    if a is None or b is None:
        return [
            _error(
                left,
                "mapping.connectivity.unknown_port",
                "one mapped terminal or layout port is absent",
            )
        ]
    if (
        a.center != b.center
        or (a.orientation - b.orientation) % 360 != 180
        or a.width != b.width
        or a.domain != b.domain
        or a.layer != b.layer
    ):
        return [
            _error(
                left,
                "mapping.connectivity.mismatch",
                f"{left_terminal!r} and {right_terminal!r} do not mate",
            )
        ]
    return []
