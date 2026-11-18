"""Preflight a model for one bounded analysis request."""

from __future__ import annotations

from collections.abc import Iterable

from waffle.contracts.component.definition import Domain, InterfaceDefinition
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.component.refs import ImplementationRef
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ModalTransform,
    Normalization,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Analysis,
    AxisCoverage,
    ExtrapolationPolicy,
    NumericInterval,
    OpticalScattering,
)
from waffle.contracts.models.model import ModelRevision


def _finding(
    severity: Severity, code: str, message: str, model: ModelRevision, *path: str | int
) -> Diagnostic:
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        subject=str(model.id),
        location=path,
    )


def _check_ranges(
    model: ModelRevision,
    ranges: dict[str, NumericInterval],
    values: dict[str, Quantity],
    role: str,
) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for name, interval in ranges.items():
        value = values.get(name)
        if value is None:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.missing_parameter",
                    f"{role} parameter {name!r} is required by applicability",
                    model,
                    role,
                    name,
                )
            )
        elif value.dimension is None or value.dimension != interval.minimum.dimension:
            found.extend(
                value.diagnostics(
                    interval.minimum.dimension,
                    subject=str(model.id),
                    location=(role, name),
                )
            )
        elif not interval.contains(value):
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.outside_applicability",
                    f"{role} parameter {name!r} is outside the applicable range",
                    model,
                    role,
                    name,
                )
            )
    return found


def _check_axis(
    model: ModelRevision, axis: AxisCoverage, operating: dict[str, Quantity]
) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    value = operating.get(axis.parameter)
    if value is None:
        return [
            _finding(
                Severity.ERROR,
                "model.missing_parameter",
                f"coverage parameter {axis.parameter!r} is missing",
                model,
                "operating",
                axis.parameter,
            )
        ]
    if value.dimension is None:
        return value.diagnostics(
            Dimension.DIMENSIONLESS,
            subject=str(model.id),
            location=("operating", axis.parameter),
        )
    dimension = (
        axis.validated.minimum.dimension
        if axis.validated
        else (axis.sampled[0].dimension if axis.sampled else None)
    )
    if dimension is not None:
        unit_errors = value.diagnostics(
            dimension, subject=str(model.id), location=("operating", axis.parameter)
        )
        if unit_errors:
            return unit_errors
    if axis.sampled is None:
        found.append(
            _finding(
                Severity.WARNING,
                "model.sampled_coverage_unknown",
                f"sampled coverage of {axis.parameter!r} is unknown",
                model,
                "applicability_spec",
                "axes",
                axis.parameter,
            )
        )
    elif not axis.sampled:
        found.append(
            _finding(
                Severity.ERROR,
                "model.no_samples",
                f"no samples of {axis.parameter!r} are available",
                model,
                "applicability_spec",
                "axes",
                axis.parameter,
            )
        )
    else:
        lo = min(sample.si for sample in axis.sampled)
        hi = max(sample.si for sample in axis.sampled)
        if not lo <= value.si <= hi:
            policy = (
                model.applicability_spec.extrapolation
                if model.applicability_spec
                else ExtrapolationPolicy.REJECT
            )
            severity = (
                Severity.ERROR
                if policy is ExtrapolationPolicy.REJECT
                else Severity.WARNING
            )
            found.append(
                _finding(
                    severity,
                    "model.extrapolation",
                    f"{axis.parameter!r} is outside sampled extent",
                    model,
                    "operating",
                    axis.parameter,
                )
            )
    if axis.validated is None:
        found.append(
            _finding(
                Severity.WARNING,
                "model.validated_coverage_unknown",
                f"validated coverage of {axis.parameter!r} is unknown",
                model,
                "applicability_spec",
                "axes",
                axis.parameter,
            )
        )
    elif not axis.validated.contains(value):
        found.append(
            _finding(
                Severity.WARNING,
                "model.outside_validated_coverage",
                f"{axis.parameter!r} is outside validated coverage",
                model,
                "operating",
                axis.parameter,
            )
        )
    return found


def check_applicability(
    model: ModelRevision,
    interface: InterfaceDefinition,
    analysis: Analysis,
    operating: OperatingValues,
    *,
    design: ResolvedDesign | None = None,
    implementation: ImplementationRef | None = None,
    mapping: ViewMapping | None = None,
    requested_channels: Iterable[tuple[str, str]] = (),
) -> list[Diagnostic]:
    """Return all actionable incompatibilities and coverage limitations.

    An empty error set means the request is executable; warnings expose unknown or
    unvalidated claims. This checks a requested subset, never synthesizes matrix entries.
    """
    found: list[Diagnostic] = []
    if model.interface != revision_of(interface):
        found.append(
            _finding(
                Severity.ERROR,
                "model.interface_mismatch",
                "model references a different interface revision",
                model,
                "interface",
            )
        )
    if model.capability is None or model.capability.analysis is not analysis:
        found.append(
            _finding(
                Severity.ERROR,
                "model.analysis_unsupported",
                f"model does not support {analysis}",
                model,
                "capability",
            )
        )
        return found
    if (
        implementation is not None
        and model.implementations
        and implementation not in model.implementations
    ):
        found.append(
            _finding(
                Severity.ERROR,
                "model.implementation_mismatch",
                "implementation revision is outside model applicability",
                model,
                "implementations",
            )
        )
    if model.implementations and implementation is None and design is None:
        found.append(
            _finding(
                Severity.ERROR,
                "model.implementation_required",
                "implementation or design is required to check pinned scope",
                model,
                "implementations",
            )
        )
    if model.designs and design is None:
        found.append(
            _finding(
                Severity.ERROR,
                "model.design_required",
                "design is required to check pinned scope",
                model,
                "designs",
            )
        )
    if design is not None:
        if model.designs and revision_of(design) not in model.designs:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.design_mismatch",
                    "design revision is outside model applicability",
                    model,
                    "designs",
                )
            )
        if model.implementations and design.implementation not in model.implementations:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.implementation_mismatch",
                    "design implementation revision is outside model applicability",
                    model,
                    "implementations",
                )
            )
    found.extend(model.operating_conditions.check(operating, subject=str(model.id)))
    spec = model.applicability_spec
    if spec is None:
        found.append(
            _finding(
                Severity.WARNING,
                "model.applicability_unknown",
                "typed applicability is unknown",
                model,
                "applicability_spec",
            )
        )
    else:
        if spec.design_ranges:
            if design is None:
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.design_required",
                        "design is required to check design ranges",
                        model,
                        "applicability_spec",
                        "design_ranges",
                    )
                )
            else:
                found.extend(
                    _check_ranges(
                        model, spec.design_ranges, design.parameters.values, "design"
                    )
                )
        operating_values = model.operating_conditions.with_defaults(operating.values)
        found.extend(
            _check_ranges(model, spec.operating_ranges, operating_values, "operating")
        )
        for axis in spec.axes:
            found.extend(_check_axis(model, axis, operating_values))
        if spec.uncertainty.status == "unknown":
            found.append(
                _finding(
                    Severity.WARNING,
                    "model.uncertainty_unknown",
                    "model uncertainty is unknown",
                    model,
                    "applicability_spec",
                    "uncertainty",
                )
            )
    if isinstance(model.capability, OpticalScattering):
        if any(
            terminal.domain is not Domain.OPTICAL for terminal in interface.terminals
        ):
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.domain_mismatch",
                    "optical scattering requires an all-optical interface",
                    model,
                    "interface",
                )
            )
        frequency = model.operating_conditions.with_defaults(operating.values).get(
            model.capability.frequency_parameter
        )
        if frequency is None:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.frequency_missing",
                    "optical scattering requires a frequency operating condition",
                    model,
                    "operating_conditions",
                )
            )
        elif frequency.dimension is not Dimension.FREQUENCY:
            found.extend(
                frequency.diagnostics(
                    Dimension.FREQUENCY,
                    subject=str(model.id),
                    location=("operating", model.capability.frequency_parameter),
                )
            )
        elif frequency.si <= 0:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.frequency_nonpositive",
                    "optical frequency must be positive",
                    model,
                    "operating",
                    model.capability.frequency_parameter,
                )
            )
        requested = set(requested_channels)
        observed = set(model.capability.observed_channels)
        for pair in sorted(requested - observed):
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.channel_unobserved",
                    f"response {pair!r} was not observed",
                    model,
                    "capability",
                    "observed_channels",
                )
            )
        if mapping is None and requested:
            found.append(
                _finding(
                    Severity.ERROR,
                    "model.mapping_required",
                    "channel requests require a view mapping",
                    model,
                    "mapping",
                )
            )
        elif mapping is not None:
            if mapping.interface != model.interface or mapping.model != revision_of(
                model
            ):
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.mapping_mismatch",
                        "mapping references a different interface or model revision",
                        model,
                        "mapping",
                    )
                )
            mapped = {
                (response.output, response.excitation)
                for response in mapping.responses
                if response.status is ResponseStatus.COMPUTED
            }
            bindings = {binding.model_channel for binding in mapping.bindings}
            if {binding.terminal for binding in mapping.bindings} != {
                terminal.name for terminal in interface.terminals
            }:
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.mapping_terminal_mismatch",
                        "mapping must bind exactly the referenced interface terminals",
                        model,
                        "mapping",
                        "bindings",
                    )
                )
            if len(bindings) != len(mapping.bindings) or len(
                {binding.terminal for binding in mapping.bindings}
            ) != len(mapping.bindings):
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.mapping_duplicate_binding",
                        "mapping has duplicate terminal or model channel bindings",
                        model,
                        "mapping",
                        "bindings",
                    )
                )
            response_pairs = [
                (response.output, response.excitation) for response in mapping.responses
            ]
            if len(set(response_pairs)) != len(response_pairs):
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.mapping_duplicate_response",
                        "mapping has duplicate response entries",
                        model,
                        "mapping",
                        "responses",
                    )
                )
            for output, excitation in sorted(requested):
                if output not in bindings or excitation not in bindings:
                    found.append(
                        _finding(
                            Severity.ERROR,
                            "model.channel_unmapped",
                            f"response {(output, excitation)!r} contains an unmapped channel",
                            model,
                            "mapping",
                            "bindings",
                        )
                    )
            used = {channel for pair in requested for channel in pair}
            for binding in mapping.bindings:
                if binding.model_channel not in used:
                    continue
                if binding.normalization is not Normalization.POWER_WAVE:
                    found.append(
                        _finding(
                            Severity.ERROR,
                            "model.normalization_unsupported",
                            f"channel {binding.model_channel!r} is not power-wave normalized",
                            model,
                            "mapping",
                            "bindings",
                        )
                    )
                if binding.modal_transform is not ModalTransform.IDENTITY:
                    found.append(
                        _finding(
                            Severity.ERROR,
                            "model.modal_transform_unsupported",
                            f"channel {binding.model_channel!r} uses an unsupported modal transform",
                            model,
                            "mapping",
                            "bindings",
                        )
                    )
                if binding.mode != 0:
                    found.append(
                        _finding(
                            Severity.ERROR,
                            "model.mode_unsupported",
                            f"channel {binding.model_channel!r} uses unsupported mode {binding.mode}",
                            model,
                            "mapping",
                            "bindings",
                        )
                    )
                if binding.reference_plane_offset_dbu != 0:
                    found.append(
                        _finding(
                            Severity.ERROR,
                            "model.reference_plane_unsupported",
                            f"channel {binding.model_channel!r} needs reference-plane correction",
                            model,
                            "mapping",
                            "bindings",
                        )
                    )
            for pair in sorted(requested - mapped):
                found.append(
                    _finding(
                        Severity.ERROR,
                        "model.channel_uncomputed",
                        f"response {pair!r} was not computed in the mapping",
                        model,
                        "mapping",
                        "responses",
                    )
                )
    return found
