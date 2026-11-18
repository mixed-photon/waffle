"""Fit one bounded power gain against a pinned simulated optical response."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.component.definition import InterfaceDefinition
from waffle.contracts.component.design import ResolvedDesign, Specimen
from waffle.contracts.component.parameters import FittedCoefficients, OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import RevisionRefField, revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import UNITS, Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Applicability,
    AxisCoverage,
    NumericInterval,
    OpticalScattering,
)
from waffle.contracts.models.model import (
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.dataset import DatasetRef, MeasurementDataset
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.experiments.measurements.ingest import read_dataset, validate_payload
from waffle.models.evaluate import (
    ChannelPair,
    OpticalEvaluator,
    evaluate_optical_scattering,
)

LIGHT_SPEED_M_S = 299_792_458.0
EVALUATOR_KEY = ("waffle.calibrated.power_gain", "1")
_REPORT_NAME = "calibration.json"
_ARTIFACT_KIND = "power_calibration"


class PowerFitSpec(BaseModel):
    """Prior bounds and an absolute transmission error limit on independent data."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    minimum_gain: float = Field(default=0.0, ge=0, le=1)
    maximum_gain: float = Field(default=1.0, gt=0, le=1)
    validation_max_error: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.minimum_gain >= self.maximum_gain:
            raise ValueError("minimum gain must be less than maximum gain")
        return self


class PowerComparison(BaseModel):
    """One comparison at a measured wavelength, with inherited simulated phase."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    role: Literal["fitting", "validation"]
    frequency_hz: float = Field(gt=0)
    measured_transmission: float = Field(ge=0, le=1)
    measured_standard_deviation: float | None = Field(default=None, ge=0)
    simulated_amplitude: tuple[float, float]
    calibrated_amplitude: tuple[float, float]

    @property
    def absolute_power_error(self) -> float:
        return abs(
            abs(complex(*self.calibrated_amplitude)) ** 2 - self.measured_transmission
        )


class PowerCalibrationReport(BaseModel):
    """Versioned comparison of simulated and measured forward power."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    method: Literal["unweighted_bounded_power_least_squares"] = (
        "unweighted_bounded_power_least_squares"
    )
    simulation: RevisionRefField
    fitting: DatasetRef
    validation: DatasetRef
    pair: tuple[str, str]
    frequency_parameter: str
    fixed_operating: OperatingValues
    spec: PowerFitSpec
    power_gain: float = Field(ge=0, le=1)
    unconstrained_gain: float = Field(ge=0)
    comparisons: tuple[PowerComparison, ...] = Field(min_length=2)
    synthetic: bool

    @property
    def validation_error(self) -> float:
        return max(
            p.absolute_power_error for p in self.comparisons if p.role == "validation"
        )

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        if {p.role for p in self.comparisons} != {"fitting", "validation"}:
            raise ValueError("fit and validation comparisons are both required")
        if self.fitting == self.validation:
            raise ValueError("fit and validation dataset revisions must differ")
        return self


@dataclass(frozen=True)
class Capture:
    dataset: MeasurementDataset
    specimen: Specimen


@dataclass(frozen=True)
class CalibratedResponse:
    model: ModelRevision
    mapping: ViewMapping
    report: PowerCalibrationReport
    artifact: ArtifactRef


def _error(code: str, message: str, subject: object) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=str(subject)
    )


def _observation(
    capture: Capture,
    mapping: ViewMapping,
    problem: PhysicalProblem,
    pair: ChannelPair,
    store: ArtifactStore,
) -> Result[tuple[tuple[float, float, float | None], ...]]:
    dataset = capture.dataset
    loaded = read_dataset(dataset, store)
    if loaded.value is None:
        return Result(None, loaded.diagnostics)
    findings = validate_payload(loaded.value, capture.specimen, mapping, problem)
    if has_errors(findings):
        return Result(None, tuple(findings))
    channels = {
        binding.model_channel: binding.measurement_channel
        for binding in mapping.bindings
    }
    output, excitation = (channels.get(channel) for channel in pair)
    traces = [
        trace
        for trace in dataset.observations
        if trace.output_channel == output and trace.excitation_channel == excitation
    ]
    incident = dataset.conditions.get("incident_power")
    if output is None or excitation is None or len(traces) != 1:
        findings.append(
            _error(
                "calibration.channel_unavailable",
                f"need measured power for {pair!r}",
                dataset.id,
            )
        )
    if (
        incident is None
        or incident.dimension is not Dimension.POWER
        or not math.isfinite(incident.si)
        or incident.si <= 0
    ):
        findings.append(
            _error(
                "calibration.incident_power",
                "positive incident_power in linear power units is required",
                dataset.id,
            )
        )
    if dataset.calibration.state != "calibrated" or not dataset.calibration.reference:
        findings.append(
            _error(
                "calibration.instrument_reference",
                "data needs a recorded incident/reference-plane calibration",
                dataset.id,
            )
        )
    if has_errors(findings):
        return Result(None, tuple(findings))
    assert incident is not None
    trace = traces[0]
    step_m = UNITS[dataset.axis.unit].scale
    scale_w = UNITS[trace.unit].scale
    points: list[tuple[float, float, float | None]] = []
    for i, wavelength in enumerate(dataset.axis.values):
        # The prepared 2D problem and sampled solver API use integer picometres.
        position_pm = wavelength * step_m / 1e-12
        if (
            not math.isfinite(position_pm)
            or position_pm < 1
            or not math.isclose(
                position_pm, round(position_pm), rel_tol=0, abs_tol=1e-6
            )
        ):
            return Result(
                None,
                (
                    _error(
                        "calibration.wavelength_grid",
                        "wavelength must be a positive whole picometre value",
                        dataset.id,
                    ),
                ),
            )
        frequency = LIGHT_SPEED_M_S / (round(position_pm) * 1e-12)
        transmission = trace.values[i] * scale_w / incident.si
        deviation = (
            None
            if trace.standard_deviation is None
            else trace.standard_deviation[i] * scale_w / incident.si
        )
        if not 0 <= transmission <= 1 or (
            deviation is not None and not math.isfinite(deviation)
        ):
            return Result(
                None,
                (
                    _error(
                        "calibration.power_range",
                        "passive transmission needs finite power ratios in [0, 1]",
                        dataset.id,
                    ),
                ),
            )
        points.append((frequency, transmission, deviation))
    return Result.of(tuple(points), findings)


def _settings(capture: Capture) -> dict[str, Quantity]:
    return {
        name: value
        for name, value in capture.dataset.conditions.items()
        if name != "incident_power"
    }


def calibrate_power_gain(
    store: ArtifactStore,
    *,
    base: ModelRevision,
    mapping: ViewMapping,
    interface: InterfaceDefinition,
    design: ResolvedDesign,
    problem: PhysicalProblem,
    fitting: Capture,
    validation: Capture,
    pair: ChannelPair,
    spec: PowerFitSpec,
    evaluators: Mapping[tuple[str, str], OpticalEvaluator],
) -> Result[CalibratedResponse]:
    """Fit passive attenuation from one channel pair and check an independent capture."""

    capability = base.capability
    if (
        not isinstance(capability, OpticalScattering)
        or not any(e.kind is EvidenceKind.SIMULATION for e in base.evidence)
        or pair[0] == pair[1]
        or pair not in capability.observed_channels
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.inference_unsupported",
                    "one observed simulated forward channel is required",
                    base.id,
                ),
            ),
        )
    if problem.design != revision_of(design) or any(
        c.dataset.design != revision_of(design) for c in (fitting, validation)
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.design_mismatch",
                    "simulation and captures must pin the same fabricated design",
                    design.id,
                ),
            ),
        )
    if (
        fitting.dataset.id == validation.dataset.id
        or fitting.dataset.raw_artifact == validation.dataset.raw_artifact
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.data_overlap",
                    "fit and validation need distinct original captures and dataset identities",
                    base.id,
                ),
            ),
        )
    if fitting.dataset.synthetic != validation.dataset.synthetic:
        return Result(
            None,
            (
                _error(
                    "calibration.data_source",
                    "fit and validation must both be synthetic or both be instrument captures",
                    base.id,
                ),
            ),
        )

    frequency_parameter = capability.frequency_parameter
    fit_conditions = _settings(fitting)
    check_conditions = _settings(validation)
    if (
        frequency_parameter in fit_conditions
        or frequency_parameter in check_conditions
        or fit_conditions != check_conditions
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.operating_mismatch",
                    "use matching fixed conditions and supply frequency through the wavelength axis",
                    base.id,
                ),
            ),
        )
    effective = base.operating_conditions.with_defaults(fit_conditions)
    effective.pop(frequency_parameter, None)
    findings: list[Diagnostic] = []
    for evidence in base.evidence:
        if isinstance(evidence.ref, ArtifactRef):
            findings.extend(store.verify(evidence.ref))
    if has_errors(findings):
        return Result(None, tuple(findings))

    data: list[
        tuple[Literal["fitting", "validation"], float, float, float | None, complex]
    ] = []
    predictions: dict[float, complex] = {}
    for role, capture in (("fitting", fitting), ("validation", validation)):
        read = _observation(capture, mapping, problem, pair, store)
        findings.extend(read.diagnostics)
        if read.value is None:
            return Result(None, tuple(findings))
        for frequency, transmission, deviation in read.value:
            if frequency not in predictions:
                response = evaluate_optical_scattering(
                    base,
                    interface,
                    OperatingValues(
                        values={
                            **effective,
                            frequency_parameter: Quantity(value=frequency, unit="Hz"),
                        }
                    ),
                    mapping,
                    (pair,),
                    evaluators,
                    design=design,
                )
                findings.extend(response.diagnostics)
                if response.value is None:
                    return Result(None, tuple(findings))
                value = response.value[pair]
                # An approximate passive guide may have a small numerical amplitude overshoot.
                if abs(value) > 1.1:
                    return Result(
                        None,
                        (
                            *findings,
                            _error(
                                "calibration.simulation_power",
                                "simulated gain is outside the bounded passive-guide approximation",
                                base.id,
                            ),
                        ),
                    )
                predictions[frequency] = value
            value = predictions[frequency]
            data.append((role, frequency, transmission, deviation, value))

    powers = [
        (abs(value) ** 2, measured)
        for role, _, measured, _, value in data
        if role == "fitting"
    ]
    energy = math.fsum(power**2 for power, _ in powers)
    if energy <= 1e-16:
        return Result(
            None,
            (
                *findings,
                _error(
                    "calibration.unidentifiable",
                    "simulated transmitted power is too small to identify attenuation",
                    base.id,
                ),
            ),
        )
    unconstrained = math.fsum(power * measured for power, measured in powers) / energy
    gain = min(spec.maximum_gain, max(spec.minimum_gain, unconstrained))
    factor = math.sqrt(gain)
    report = PowerCalibrationReport(
        simulation=revision_of(base),
        fitting=revision_of(fitting.dataset),
        validation=revision_of(validation.dataset),
        pair=pair,
        frequency_parameter=frequency_parameter,
        fixed_operating=OperatingValues(values=effective),
        spec=spec,
        power_gain=gain,
        unconstrained_gain=unconstrained,
        synthetic=fitting.dataset.synthetic,
        comparisons=tuple(
            PowerComparison(
                role=role,
                frequency_hz=f,
                measured_transmission=measured,
                measured_standard_deviation=deviation,
                simulated_amplitude=(value.real, value.imag),
                calibrated_amplitude=(factor * value.real, factor * value.imag),
            )
            for role, f, measured, deviation, value in data
        ),
    )
    inputs = (
        InputLink(role="simulation_model", target=report.simulation),
        InputLink(role="fitting_dataset", target=report.fitting),
        InputLink(role="validation_dataset", target=report.validation),
        InputLink(role="fitting_data", target=fitting.dataset.data_artifact),
        InputLink(role="validation_data", target=validation.dataset.data_artifact),
    )
    operation = OperationInfo(
        name="calibration.power_gain",
        version="1",
        parameters_digest=content_hash(
            {"spec": spec.model_dump(mode="json"), "pair": pair}
        ),
    )
    try:
        with store.attempt(operation, inputs) as session:
            writer = session.new_artifact(
                _ARTIFACT_KIND, {"simulation": str(report.simulation)}
            )
            writer.write_json(_REPORT_NAME, report.model_dump(mode="json"))
            artifact = writer.commit()
    except ArtifactError as exc:
        return Result(None, (*findings, *exc.diagnostics))
    if report.validation_error > spec.validation_max_error or any(
        abs(complex(*p.calibrated_amplitude)) > 1 for p in report.comparisons
    ):
        return Result(
            None,
            (
                *findings,
                _error(
                    "calibration.validation_failed",
                    f"power error {report.validation_error:.6g} or passivity failed; comparison {artifact}",
                    base.id,
                ),
            ),
        )

    original_applicability = base.applicability_spec or Applicability()
    samples = tuple(
        Quantity(value=f, unit="Hz")
        for f in sorted({p.frequency_hz for p in report.comparisons})
    )
    model = base.model_copy(
        update={
            "designs": (revision_of(design),),
            "payload": EvaluatorPayload(
                name=EVALUATOR_KEY[0], version=EVALUATOR_KEY[1]
            ),
            "coefficients": FittedCoefficients(
                values={"power_gain": Quantity(value=gain, unit="1")}
            ),
            "capability": capability.model_copy(update={"observed_channels": (pair,)}),
            "applicability_spec": Applicability(
                design_ranges=original_applicability.design_ranges,
                operating_ranges={
                    **original_applicability.operating_ranges,
                    **{
                        name: NumericInterval(minimum=q, maximum=q)
                        for name, q in effective.items()
                    },
                },
                axes=(AxisCoverage(parameter=frequency_parameter, sampled=samples),),
            ),
            "evidence": (
                EvidenceRef(kind=EvidenceKind.SIMULATION, ref=report.simulation),
                EvidenceRef(kind=EvidenceKind.MEASUREMENT, ref=report.fitting),
                EvidenceRef(kind=EvidenceKind.MEASUREMENT, ref=report.validation),
                EvidenceRef(kind=EvidenceKind.VALIDATION, ref=artifact),
            ),
            "metadata": {
                **base.metadata,
                "calibration": "power_gain_v1",
                "phase": "inherited_from_simulation",
            },
        }
    )
    calibrated_mapping = mapping.model_copy(
        update={
            "model": revision_of(model),
            "responses": tuple(
                ResponseChannel(
                    output=response.output,
                    excitation=response.excitation,
                    status=ResponseStatus.COMPUTED
                    if (response.output, response.excitation) == pair
                    else ResponseStatus.UNCOMPUTED,
                )
                for response in mapping.responses
            ),
        }
    )
    return Result.of(
        CalibratedResponse(model, calibrated_mapping, report, artifact), findings
    )


def calibrated_evaluator(
    store: ArtifactStore, model: ModelRevision
) -> Result[OpticalEvaluator]:
    """Load the exact calibrated samples and dispatch through the public model evaluator."""
    payload = model.payload
    artifacts = [
        item.ref
        for item in model.evidence
        if item.kind is EvidenceKind.VALIDATION
        and isinstance(item.ref, ArtifactRef)
        and item.ref.kind == _ARTIFACT_KIND
    ]
    if (
        not isinstance(payload, EvaluatorPayload)
        or (payload.name, payload.version) != EVALUATOR_KEY
        or len(artifacts) != 1
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.payload",
                    "expected a pinned power calibration report",
                    model.id,
                ),
            ),
        )
    try:
        report = PowerCalibrationReport.model_validate_json(
            store.read_bytes(artifacts[0], _REPORT_NAME)
        )
    except ArtifactError as exc:
        return Result(None, exc.diagnostics)
    except ValidationError as exc:
        return Result(
            None, (_error("calibration.report_invalid", str(exc), artifacts[0]),)
        )
    if model.coefficients.values.get("power_gain") != Quantity(
        value=report.power_gain, unit="1"
    ):
        return Result(
            None,
            (
                _error(
                    "calibration.payload_mismatch",
                    "report and calibrated model power gains differ",
                    model.id,
                ),
            ),
        )
    points = {
        point.frequency_hz: complex(*point.calibrated_amplitude)
        for point in report.comparisons
    }
    pinned = revision_of(model)

    def evaluate(
        supplied: ModelRevision,
        conditions: OperatingValues,
        pairs: frozenset[ChannelPair],
    ) -> Mapping[ChannelPair, complex]:
        frequency = conditions.values[report.frequency_parameter].si
        if (
            revision_of(supplied) != pinned
            or report.pair not in pairs
            or frequency not in points
        ):
            return {}
        return {report.pair: points[frequency]}

    return Result.of(evaluate)
