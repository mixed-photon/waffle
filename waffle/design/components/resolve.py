"""Resolve a component implementation into a `ResolvedDesign`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from waffle.contracts.component.definition import (
    ComponentDefinition,
    GeneratorSource,
    PhysicalImplementation,
)
from waffle.contracts.component.design import ResolvedDesign, ToolPin
from waffle.contracts.component.parameters import DesignValues
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.technology.requirements import check_requirement


def resolve_design(
    *,
    id: EntityId,
    definition: ComponentDefinition,
    implementation: PhysicalImplementation,
    values: DesignValues,
    technology: RevisionRef,
    tools: Iterable[ToolPin] = (),
    metadata: Mapping[str, str] | None = None,
    technology_record: TechnologyRevision | None = None,
) -> Result[ResolvedDesign]:
    """Check the inputs against each other and return the pinned design.

    Defaults are filled in so the design stores every parameter explicitly. Tool pins are
    sorted by name; a generator-sourced implementation needs a pin with the generator's
    exact name and version. When the pinned technology's record is supplied, it must be that
    revision and contain the layers the implementation requires.
    """
    subject = str(id)
    pins = sorted(tools, key=lambda t: t.name)
    found = [
        d.model_copy(update={"location": ("parameters", *d.location)})
        for d in definition.design_parameters.check(values, subject)
    ]
    if implementation.component != revision_of(definition):
        found.append(
            _error(
                "design.component_mismatch",
                f"implementation targets {implementation.component}, not {revision_of(definition)}",
                subject,
                "implementation",
            )
        )
    if technology.entity != implementation.technology.technology:
        found.append(
            _error(
                "design.technology_mismatch",
                f"implementation requires {implementation.technology.technology}, got {technology}",
                subject,
                "technology",
            )
        )
    if technology_record is not None:
        if revision_of(technology_record) != technology:
            found.append(
                _error(
                    "design.technology_record_mismatch",
                    f"technology record is {revision_of(technology_record)}, not {technology}",
                    subject,
                    "technology",
                )
            )
        else:
            found.extend(
                d.model_copy(update={"subject": subject, "location": ("technology", *d.location)})
                for d in check_requirement(implementation.technology, technology_record)
            )
    if len({p.name for p in pins}) != len(pins):
        found.append(
            _error("design.duplicate_tool_pin", "tool names must be unique", subject, "tools")
        )
    source = implementation.source
    if isinstance(source, GeneratorSource) and not any(
        p.name == source.name and p.version == source.version for p in pins
    ):
        found.append(
            _error(
                "design.missing_tool_pin",
                f"generator {source.name} {source.version} is not pinned",
                subject,
                "tools",
            )
        )
    if any(d.severity is Severity.ERROR for d in found):
        return Result(None, tuple(found))
    complete = definition.design_parameters.with_defaults(values.values)
    design = ResolvedDesign(
        id=id,
        implementation=revision_of(implementation),
        parameters=DesignValues(values=complete),
        technology=technology,
        tools=tuple(pins),
        metadata=dict(metadata or {}),
    )
    return Result.of(design, found)


def _error(code: str, message: str, subject: str, *location: str) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject, location=location
    )
