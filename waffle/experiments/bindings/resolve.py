"""Choose one pinned model per circuit instance with inspectable decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from waffle.contracts.component.definition import (
    ComponentDefinition,
    InterfaceDefinition,
    PhysicalImplementation,
)
from waffle.contracts.component.design import Circuit, ResolvedDesign
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.experiments.binding import ExperimentBinding, InstanceBinding
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.models.capability import Analysis
from waffle.contracts.models.model import (
    ArtifactPayload,
    EvaluatorPayload,
    ModelRevision,
)
from waffle.models.applicability import check_applicability


@dataclass(frozen=True)
class CandidateDecision:
    instance: str
    model: RevisionRef
    accepted: bool
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class SelectionResult:
    binding: ExperimentBinding | None
    decisions: tuple[CandidateDecision, ...]
    diagnostics: tuple[Diagnostic, ...]


def _error(code: str, message: str, subject: str) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject
    )


def resolve_binding(
    binding_id: EntityId,
    circuit: Circuit,
    designs: Mapping[RevisionRef, ResolvedDesign],
    implementations: Mapping[RevisionRef, PhysicalImplementation],
    components: Mapping[RevisionRef, ComponentDefinition],
    interfaces: Mapping[RevisionRef, InterfaceDefinition],
    candidates: Mapping[str, tuple[ModelRevision, ...]],
    mappings: Mapping[RevisionRef, ViewMapping],
    operating: Mapping[str, OperatingValues],
    requested_channels: Mapping[str, tuple[tuple[str, str], ...]],
    selected: Mapping[str, RevisionRef] | None = None,
) -> SelectionResult:
    """Resolve explicit choices, or accept a unique compatible candidate per instance.

    Every record lookup is keyed and verified by exact revision. Candidate decisions
    retain preflight diagnostics for accepted and rejected alternatives.
    """
    choices = selected or {}
    decisions: list[CandidateDecision] = []
    findings: list[Diagnostic] = []
    entries: list[InstanceBinding] = []
    names = {instance.name for instance in circuit.instances}
    for name in sorted(choices.keys() - names):
        findings.append(
            _error(
                "binding.unknown_instance",
                f"selection names unknown instance {name!r}",
                str(binding_id),
            )
        )
    for instance in circuit.instances:
        subject = f"{circuit.id}.{instance.name}"
        design = designs.get(instance.design)
        if design is None or revision_of(design) != instance.design:
            findings.append(
                _error(
                    "binding.design_unavailable",
                    "exact design revision is unavailable",
                    subject,
                )
            )
            continue
        implementation = implementations.get(design.implementation)
        if (
            implementation is None
            or revision_of(implementation) != design.implementation
        ):
            findings.append(
                _error(
                    "binding.implementation_unavailable",
                    "exact implementation revision is unavailable",
                    subject,
                )
            )
            continue
        component = components.get(implementation.component)
        if component is None or revision_of(component) != implementation.component:
            findings.append(
                _error(
                    "binding.component_unavailable",
                    "exact component revision is unavailable",
                    subject,
                )
            )
            continue
        interface = interfaces.get(component.interface)
        if interface is None or revision_of(interface) != component.interface:
            findings.append(
                _error(
                    "binding.interface_unavailable",
                    "exact interface revision is unavailable",
                    subject,
                )
            )
            continue
        conditions = operating.get(instance.name)
        if conditions is None:
            findings.append(
                _error(
                    "binding.operating_missing",
                    "operating conditions are missing",
                    subject,
                )
            )
            continue
        model_options = candidates.get(instance.name, ())
        accepted: list[tuple[ModelRevision, ViewMapping]] = []
        seen_models: set[RevisionRef] = set()
        for model in model_options:
            model_ref = revision_of(model)
            if model_ref in seen_models:
                continue
            seen_models.add(model_ref)
            mapping = mappings.get(model_ref)
            model_findings = check_applicability(
                model,
                interface,
                Analysis.OPTICAL_SCATTERING,
                conditions,
                design=design,
                implementation=design.implementation,
                mapping=mapping,
                requested_channels=requested_channels.get(instance.name, ()),
            )
            if mapping is None:
                model_findings.append(
                    _error(
                        "binding.mapping_unavailable",
                        "no mapping for this model revision",
                        subject,
                    )
                )
            valid = not has_errors(model_findings)
            decisions.append(
                CandidateDecision(
                    instance.name, model_ref, valid, tuple(model_findings)
                )
            )
            if valid and mapping is not None:
                accepted.append((model, mapping))
        explicit = choices.get(instance.name)
        if explicit is not None:
            matched = [
                (model, mapping)
                for model, mapping in accepted
                if revision_of(model) == explicit
            ]
            if not matched:
                findings.append(
                    _error(
                        "binding.selection_rejected",
                        f"selected model {explicit} is unavailable or incompatible",
                        subject,
                    )
                )
                continue
            chosen_model, chosen_mapping = matched[0]
        elif len(accepted) == 1:
            chosen_model, chosen_mapping = accepted[0]
        elif len(accepted) == 0:
            findings.append(
                _error(
                    "binding.no_compatible_model",
                    "no compatible model is available",
                    subject,
                )
            )
            continue
        else:
            findings.append(
                _error(
                    "binding.ambiguous_model",
                    "multiple compatible models require explicit selection",
                    subject,
                )
            )
            continue
        payload = chosen_model.payload
        entries.append(
            InstanceBinding(
                instance=instance.name,
                design=instance.design,
                implementation=design.implementation,
                component=implementation.component,
                interface=component.interface,
                model=revision_of(chosen_model),
                mapping=revision_of(chosen_mapping),
                analysis=Analysis.OPTICAL_SCATTERING,
                operating=OperatingValues(
                    values=chosen_model.operating_conditions.with_defaults(
                        conditions.values
                    )
                ),
                requested_channels=requested_channels.get(instance.name, ()),
                evaluator_name=payload.name
                if isinstance(payload, EvaluatorPayload)
                else None,
                evaluator_version=payload.version
                if isinstance(payload, EvaluatorPayload)
                else None,
                payload_artifact=payload.artifact
                if isinstance(payload, ArtifactPayload)
                else None,
            )
        )
    binding = (
        None
        if findings
        else ExperimentBinding(
            id=binding_id, circuit=revision_of(circuit), instances=tuple(entries)
        )
    )
    return SelectionResult(binding, tuple(decisions), tuple(findings))
