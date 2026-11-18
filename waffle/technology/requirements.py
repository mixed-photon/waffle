"""Check an implementation's technology requirement against a technology revision."""

from __future__ import annotations

from waffle.contracts.component.definition import TechnologyRequirement
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.technology.revision import TechnologyRevision


def check_requirement(
    requirement: TechnologyRequirement, technology: TechnologyRevision
) -> list[Diagnostic]:
    """`technology.requirement_id_mismatch` if `technology` is another technology, and
    `technology.requirement_unknown_layer` for each required layer name it lacks."""
    subject = str(technology.id)
    found: list[Diagnostic] = []
    if requirement.technology != technology.id:
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="technology.requirement_id_mismatch",
                message=f"requires {requirement.technology}, got {technology.id}",
                subject=subject,
                location=("technology",),
            )
        )
    for i, name in enumerate(requirement.layers):
        if technology.layer(name) is None:
            found.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="technology.requirement_unknown_layer",
                    message=f"required layer {name!r} is not in the technology",
                    subject=subject,
                    location=("layers", i),
                )
            )
    return found
