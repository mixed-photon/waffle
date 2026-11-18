"""Dispatch a pinned optical evaluator and validate its bounded response."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping

from waffle.contracts.component.definition import InterfaceDefinition
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.component.refs import ImplementationRef
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.result import Result
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.models.capability import Analysis
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.models.applicability import check_applicability

type ChannelPair = tuple[str, str]
type ScatteringResponse = dict[ChannelPair, complex]
type OpticalEvaluator = Callable[
    [ModelRevision, OperatingValues, frozenset[ChannelPair]],
    Mapping[ChannelPair, complex],
]


def evaluate_optical_scattering(
    model: ModelRevision,
    interface: InterfaceDefinition,
    operating: OperatingValues,
    mapping: ViewMapping,
    requested_channels: Iterable[ChannelPair],
    evaluators: Mapping[tuple[str, str], OpticalEvaluator],
    *,
    design: ResolvedDesign | None = None,
    implementation: ImplementationRef | None = None,
) -> Result[ScatteringResponse]:
    """Evaluate only requested, observed S elements through an exact evaluator pin.

    Evaluators are supplied by application assembly. This layer owns no analytic
    physics; an evaluator receives the model, conditions, and requested pairs.
    """
    requested = frozenset(requested_channels)
    findings = check_applicability(
        model,
        interface,
        Analysis.OPTICAL_SCATTERING,
        operating,
        design=design,
        implementation=implementation,
        mapping=mapping,
        requested_channels=requested,
    )
    if has_errors(findings):
        return Result(None, tuple(findings))
    if not isinstance(model.payload, EvaluatorPayload):
        findings.append(
            _error(
                model,
                "model.payload_unsupported",
                "optical evaluation requires an evaluator payload",
            )
        )
        return Result(None, tuple(findings))
    key = (model.payload.name, model.payload.version)
    evaluator = evaluators.get(key)
    if evaluator is None:
        findings.append(
            _error(
                model,
                "model.evaluator_unavailable",
                f"evaluator {key!r} is unavailable",
            )
        )
        return Result(None, tuple(findings))
    effective_operating = OperatingValues(
        values=model.operating_conditions.with_defaults(operating.values)
    )
    raw = evaluator(model, effective_operating, requested)
    if not isinstance(raw, Mapping):
        findings.append(
            _error(
                model,
                "model.invalid_response",
                "evaluator must return a channel mapping",
            )
        )
        return Result(None, tuple(findings))
    produced: set[ChannelPair] = set()
    for pair in raw:
        if not (
            isinstance(pair, tuple)
            and len(pair) == 2
            and all(isinstance(channel, str) for channel in pair)
        ):
            findings.append(
                _error(
                    model,
                    "model.response_invalid_key",
                    f"invalid response channel key {pair!r}",
                )
            )
        else:
            produced.add(pair)
    for pair in sorted(requested - produced):
        findings.append(
            _error(
                model,
                "model.response_missing",
                f"evaluator omitted requested response {pair!r}",
            )
        )
    for pair in sorted(produced - requested):
        findings.append(
            _error(
                model,
                "model.response_unrequested",
                f"evaluator returned unrequested response {pair!r}",
            )
        )
    for pair, value in raw.items():
        if (
            not isinstance(value, complex)
            or not math.isfinite(value.real)
            or not math.isfinite(value.imag)
        ):
            findings.append(
                _error(
                    model,
                    "model.response_invalid",
                    f"response {pair!r} must be a finite complex amplitude",
                )
            )
    return Result.of(dict(raw), findings)


def _error(model: ModelRevision, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=str(model.id)
    )
