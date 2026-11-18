"""Validate, publish, and process bounded optical power measurements."""

from __future__ import annotations

import json
import math

from pydantic import ValidationError

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.component.definition import Domain
from waffle.contracts.component.design import Specimen
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import UNITS, Dimension
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.results.dataset import MeasurementDataset, MeasurementPayload
from waffle.contracts.results.provenance import InputLink, OperationInfo


def _error(code: str, message: str, *location: str | int) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, location=location
    )


def _parse(raw: bytes) -> Result[MeasurementPayload]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Result(None, (_error("measurement.invalid_json", str(exc)),))
    try:
        return Result.of(MeasurementPayload.model_validate(payload))
    except ValidationError as exc:
        return Result(
            None,
            tuple(
                _error("measurement.invalid_payload", issue["msg"], *issue["loc"])
                for issue in exc.errors()
            ),
        )


def validate_payload(
    payload: MeasurementPayload,
    specimen: Specimen,
    mapping: ViewMapping,
    problem: PhysicalProblem,
) -> list[Diagnostic]:
    """Semantic checks that need the exact referenced specimen and channel map."""

    found: list[Diagnostic] = []
    if payload.specimen != revision_of(specimen):
        found.append(
            _error(
                "measurement.specimen_mismatch",
                "specimen revision differs from the supplied record",
                "specimen",
            )
        )
    if payload.design != specimen.design:
        found.append(
            _error(
                "measurement.design_mismatch",
                "fabricated design differs from specimen.design",
                "design",
            )
        )
    if payload.mapping != revision_of(mapping):
        found.append(
            _error(
                "measurement.mapping_mismatch",
                "mapping revision differs from the supplied record",
                "mapping",
            )
        )
    if mapping.problem != revision_of(problem):
        found.append(
            _error(
                "measurement.problem_mismatch",
                "mapping does not reference the supplied physical problem revision",
                "mapping",
            )
        )
    if problem.design != specimen.design:
        found.append(
            _error(
                "measurement.design_mismatch",
                "physical problem does not reference the specimen's fabricated design revision",
                "mapping",
            )
        )
    if mapping.layout != problem.layout:
        found.append(
            _error(
                "measurement.invalid_mapping",
                "mapping and problem layouts differ",
                "mapping",
            )
        )
    physical_ports = {port.path: port for port in problem.description.ports}
    for binding in mapping.bindings:
        if binding.measurement_channel is None:
            continue
        port = physical_ports.get(binding.simulation_port)
        if port is None or port.domain is not Domain.OPTICAL:
            found.append(
                _error(
                    "measurement.invalid_mapping",
                    f"measurement channel {binding.measurement_channel!r} needs an optical physical port",
                    "mapping",
                )
            )
    channels = [
        binding.measurement_channel
        for binding in mapping.bindings
        if binding.measurement_channel is not None
    ]
    if len(channels) != len(set(channels)):
        found.append(
            _error(
                "measurement.invalid_mapping",
                "measurement channels in mapping must be unique",
                "mapping",
            )
        )
    valid = set(channels)
    if not valid:
        found.append(
            _error(
                "measurement.invalid_mapping",
                "mapping declares no measurement channels",
                "mapping",
            )
        )
    axis = payload.axis
    if (
        UNITS.get(axis.unit) is None
        or UNITS[axis.unit].dimension is not Dimension.LENGTH
    ):
        found.append(
            _error(
                "measurement.axis_unit",
                "wavelength axis requires a length unit",
                "axis",
                "unit",
            )
        )
    if any(not math.isfinite(v) or v <= 0 for v in axis.values) or any(
        a >= b for a, b in zip(axis.values, axis.values[1:])
    ):
        found.append(
            _error(
                "measurement.axis_values",
                "wavelengths must be positive, finite, and strictly increasing",
                "axis",
                "values",
            )
        )
    pairs: set[tuple[str, str | None]] = set()
    for index, observation in enumerate(payload.observations):
        at = ("observations", index)
        pair = (observation.output_channel, observation.excitation_channel)
        if pair in pairs:
            found.append(
                _error(
                    "measurement.duplicate_observation",
                    f"duplicate channel pair {pair}",
                    *at,
                )
            )
        pairs.add(pair)
        for channel in pair:
            if channel is not None and channel not in valid:
                found.append(
                    _error(
                        "measurement.unknown_channel",
                        f"channel {channel!r} is absent from mapping",
                        *at,
                    )
                )
        if (
            UNITS.get(observation.unit) is None
            or UNITS[observation.unit].dimension is not Dimension.POWER
        ):
            found.append(
                _error(
                    "measurement.power_unit",
                    "optical power requires a linear power unit",
                    *at,
                    "unit",
                )
            )
        if len(observation.values) != len(axis.values) or any(
            not math.isfinite(v) or v < 0 for v in observation.values
        ):
            found.append(
                _error(
                    "measurement.power_values",
                    "power values must match the axis and be finite and nonnegative",
                    *at,
                    "values",
                )
            )
        sd = observation.standard_deviation
        if sd is not None and (
            len(sd) != len(axis.values)
            or any(not math.isfinite(v) or v < 0 for v in sd)
        ):
            found.append(
                _error(
                    "measurement.uncertainty",
                    "standard deviations must match the axis and be finite and nonnegative",
                    *at,
                    "standard_deviation",
                )
            )
    for name, quantity in payload.conditions.items():
        if quantity.dimension is None:
            found.append(
                _error(
                    "measurement.condition_unit",
                    f"unknown unit {quantity.unit!r}",
                    "conditions",
                    name,
                )
            )
    if payload.calibration.state == "calibrated" and not payload.calibration.method:
        found.append(
            _error(
                "measurement.calibration",
                "calibrated data requires a method",
                "calibration",
            )
        )
    return found


def _operation(name: str, parameters: object) -> OperationInfo:
    return OperationInfo(
        name=name, version="1", parameters_digest=content_hash(parameters)
    )


def import_measurement(
    raw: bytes,
    *,
    id: EntityId,
    specimen: Specimen,
    mapping: ViewMapping,
    problem: PhysicalProblem,
    store: ArtifactStore,
) -> Result[MeasurementDataset]:
    """Publish the original JSON bytes and a dataset record after full validation."""

    parsed = _parse(raw)
    if parsed.value is None:
        return Result(None, parsed.diagnostics)
    payload = parsed.value
    found = validate_payload(payload, specimen, mapping, problem)
    if has_errors(found):
        return Result(None, tuple(found))
    inputs = (
        InputLink(role="specimen", target=payload.specimen),
        InputLink(role="fabricated_design", target=payload.design),
        InputLink(role="channel_mapping", target=payload.mapping),
    )
    try:
        with store.attempt(
            _operation("measurement.import_json", {"schema_version": 1}), inputs
        ) as session:
            writer = session.new_artifact(
                "measurement_raw",
                {
                    "specimen": str(payload.specimen),
                    "mapping": str(payload.mapping),
                    "synthetic": payload.synthetic,
                },
            )
            writer.write_bytes("source.json", raw, "application/json")
            artifact = writer.commit()
    except ArtifactError as exc:
        return Result(None, tuple(exc.diagnostics))
    return Result.of(
        MeasurementDataset(
            id=id,
            specimen=payload.specimen,
            design=payload.design,
            mapping=payload.mapping,
            raw_artifact=artifact,
            data_artifact=artifact,
            axis=payload.axis,
            observations=payload.observations,
            conditions=payload.conditions,
            calibration=payload.calibration,
            synthetic=payload.synthetic,
        )
    )


def read_dataset(
    dataset: MeasurementDataset, store: ArtifactStore
) -> Result[MeasurementPayload]:
    """Read verified data and check it against the pinned dataset metadata."""
    is_raw = dataset.data_artifact == dataset.raw_artifact
    if dataset.raw_artifact.kind != "measurement_raw" or dataset.data_artifact.kind != (
        "measurement_raw" if is_raw else "measurement_processed"
    ):
        return Result(
            None,
            (
                _error(
                    "measurement.source_kind",
                    "expected a raw measurement and raw or processed measurement data",
                ),
            ),
        )
    try:
        if not is_raw:
            store.read_bytes(dataset.raw_artifact, "source.json")
        source_name = "source.json" if is_raw else "data.json"
        source = store.read_bytes(dataset.data_artifact, source_name)
    except ArtifactError as exc:
        return Result(None, tuple(exc.diagnostics))
    parsed = _parse(source)
    if parsed.value is None:
        return Result(None, parsed.diagnostics)
    expected = MeasurementPayload(
        specimen=dataset.specimen,
        design=dataset.design,
        mapping=dataset.mapping,
        axis=dataset.axis,
        observations=dataset.observations,
        conditions=dataset.conditions,
        calibration=dataset.calibration,
        synthetic=dataset.synthetic,
    )
    if parsed.value != expected:
        return Result(
            None,
            (
                _error(
                    "measurement.source_mismatch",
                    "dataset metadata differs from its source artifact",
                ),
            ),
        )
    return parsed


def scale_power(
    dataset: MeasurementDataset,
    factor: float,
    *,
    store: ArtifactStore,
) -> Result[MeasurementDataset]:
    """Publish a derived artifact and revision; preserve unknown uncertainty as unknown."""

    if not math.isfinite(factor) or factor < 0:
        return Result(
            None,
            (
                _error(
                    "measurement.invalid_scale",
                    "power scale must be finite and nonnegative",
                ),
            ),
        )
    source = read_dataset(dataset, store)
    if source.value is None:
        return Result(None, source.diagnostics)
    observations = tuple(
        obs.model_copy(
            update={
                "values": tuple(value * factor for value in obs.values),
                "standard_deviation": None
                if obs.standard_deviation is None
                else tuple(value * factor for value in obs.standard_deviation),
            }
        )
        for obs in source.value.observations
    )
    if any(
        not math.isfinite(value)
        for obs in observations
        for value in (*obs.values, *(obs.standard_deviation or ()))
    ):
        return Result(
            None,
            (_error("measurement.invalid_scale", "scaled values must remain finite"),),
        )
    payload = MeasurementPayload(
        specimen=dataset.specimen,
        design=dataset.design,
        mapping=dataset.mapping,
        axis=dataset.axis,
        observations=observations,
        conditions=dataset.conditions,
        calibration=dataset.calibration,
        synthetic=dataset.synthetic,
    )
    inputs = (
        InputLink(role="source_data", target=dataset.data_artifact),
        InputLink(role="raw_data", target=dataset.raw_artifact),
        InputLink(role="parent_dataset", target=revision_of(dataset)),
    )
    try:
        with store.attempt(
            _operation("measurement.scale_power", {"factor": factor}), inputs
        ) as session:
            writer = session.new_artifact(
                "measurement_processed",
                {"parent": str(revision_of(dataset)), "factor": factor},
            )
            writer.write_json("data.json", payload.model_dump(mode="json"))
            artifact = writer.commit()
    except ArtifactError as exc:
        return Result(None, tuple(exc.diagnostics))
    return Result.of(
        dataset.model_copy(
            update={
                "data_artifact": artifact,
                "parent": revision_of(dataset),
                "observations": observations,
            }
        )
    )
