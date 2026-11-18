"""Bounded BeamZ experiment on the reference two-by-two TM MMI."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.characterization.validation.compare import compare_complex
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
)
from waffle.contracts.characterization.report import Comparison, ValidationReport
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.mappings.mapping import (
    ModalTransform,
    Normalization,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Applicability,
    AxisCoverage,
    OpticalScattering,
)
from waffle.contracts.models.model import (
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.physics.approximations import (
    EffectiveIndexReduction,
    Polarization,
)
from waffle.contracts.physics.problem import (
    Boundaries2D,
    BoundaryCondition,
    Domain2D,
    PhysicalProblem,
    PortPlane,
    Reduced2D,
)
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.execution.worker import ExecutionResult, run_isolated
from waffle.models.evaluate import ChannelPair, OpticalEvaluator
from waffle.simulation.common.backend import CancellationToken, RunState
from waffle.simulation.photonics.device_analysis.slab import (
    SlabLayer,
    SlabStack,
    effective_index,
)

_WAVELENGTH_PM = 1550000
_WAVELENGTH_M = _WAVELENGTH_PM * 1e-12
_FREQUENCY = 299792458.0 / _WAVELENGTH_M
_CHANNELS = ("o1", "o2", "o3", "o4")
_OBSERVED = (("o3", "o1"), ("o4", "o1"))
_OFFSETS = (600, 825, 1175, 1525, 1975, 2550, 3150, 3750)
_SETTINGS = (
    ("fine", 20000, 10500, 1000000),
    ("coarse", 25000, 10500, 1000000),
    ("time", 20000, 18000, 1000000),
    ("boundary", 20000, 10500, 1500000),
)


def _reference(problem: PhysicalProblem) -> tuple[Reduced2D, int, int]:
    d = problem.description
    if (
        not isinstance(d, Reduced2D)
        or problem.dbu_pm != 1000
        or len(d.regions) != 1
        or len(d.regions[0].polygons) != 5
        or len(d.ports) != 4
    ):
        raise ValueError("MMI recipe requires the 1 nm dbu reference MMI")
    reduction = next(
        (a for a in problem.approximations if isinstance(a, EffectiveIndexReduction)),
        None,
    )
    ports = {port.path: port for port in d.ports}
    x0 = min(port.center[0] for port in d.ports)
    x1 = max(port.center[0] for port in d.ports)
    if (
        reduction is None
        or reduction.polarization is not Polarization.TM
        or reduction.wavelength_pm != _WAVELENGTH_PM
        or set(ports) != set(_CHANNELS)
        or x1 - x0 != 25500
        or tuple(
            (ports[p].center, ports[p].orientation, ports[p].width) for p in _CHANNELS
        )
        != (
            ((x0, -650), 180, 500),
            ((x0, 650), 180, 500),
            ((x1, 650), 0, 500),
            ((x1, -650), 0, 500),
        )
    ):
        raise ValueError(
            "MMI recipe requires the original four reference planes and the 1550 nm TM reduction"
        )
    polygons = d.regions[0].polygons
    if any(not x0 <= x <= x1 for polygon in polygons for x, _ in polygon.points):
        raise ValueError("MMI geometry must lie between the original port planes")
    mirror = Transform(mirror=True)
    original = Counter(polygon.points for polygon in polygons)
    reflected = Counter(polygon.transformed(mirror).points for polygon in polygons)
    if original != reflected:
        raise ValueError(
            "MMI mirror reference requires transversely symmetric geometry"
        )
    return d, x0, x1


def _acquire(
    problem: PhysicalProblem,
    *,
    command: tuple[str, ...],
    root: Path,
    store: ArtifactStore,
    cancellation: CancellationToken | None,
) -> dict[str, tuple[ExecutionResult, ...]]:
    d, x0, x1 = _reference(problem)
    lo, hi = (x0 - 11000, -3300), (x1 + 11000, 3300)
    leads = tuple(
        Polygon(
            points=((start, y - 250), (end, y - 250), (end, y + 250), (start, y + 250))
        )
        for start, end in ((lo[0], x0), (x1, hi[0]))
        for y in (-650, 650)
    )
    region = d.regions[0].model_copy(
        update={"polygons": (*d.regions[0].polygons, *leads)}
    )
    planes = tuple(
        PortPlane(
            path=f"{side}{i}",
            layer=region.layer,
            domain=d.ports[0].domain,
            center=(x, 0),
            width=3400,
            orientation=0,
        )
        for side, positions in (
            ("left", tuple(x0 - offset for offset in _OFFSETS)),
            ("right", tuple(x1 + offset for offset in _OFFSETS)),
        )
        for i, x in enumerate(positions)
    )
    executions = {}
    for name, spacing, steps, pml in _SETTINGS:
        boundary = BoundaryCondition(thickness_pm=pml)
        derived = problem.model_copy(
            update={
                "id": EntityId("physical_problem", f"mmi_{name}"),
                "description": d.model_copy(
                    update={
                        "domain": Domain2D(
                            lo=lo,
                            hi=hi,
                            boundaries=Boundaries2D(
                                x_min=boundary,
                                x_max=boundary,
                                y_min=boundary,
                                y_max=boundary,
                            ),
                        ),
                        "regions": (region,),
                        "ports": planes,
                    }
                ),
                "metadata": {
                    **problem.metadata,
                    "analysis_extension": "Four uniform access guides continued through the x PML",
                    "seed_problem": str(revision_of(problem)),
                },
            }
        )
        runs = []
        for excitation, y in (("o1", -650), ("o2", 650)):
            request = AnalysisRequest(
                id=f"analysis_request:mmi_{name}_{excitation}",
                problem=revision_of(derived),
                polarization=Polarization.TM,
                excitations=(
                    Excitation(
                        kind="point",
                        center_dbu=(x0 - 8000, y),
                        wavelength_pm=_WAVELENGTH_PM,
                    ),
                ),
                observables=tuple(
                    Observable(
                        name=plane.path,
                        kind="raw_field",
                        field_component="Ez",
                        port=plane.path,
                    )
                    for plane in planes
                ),
                numerical=NumericalSettings(
                    grid_spacing_pm=spacing, time_step_as=40, steps=steps
                ),
                backend_settings=BackendSettings(
                    backend="beamz",
                    version="0.6.2",
                    schema_version=1,
                    parameters={
                        "source_width_pm": 100000,
                        "pulse_center_as": 40000,
                        "pulse_width_as": 12000,
                    },
                ),
            )
            execution = run_isolated(
                derived,
                request,
                command=command,
                root=root,
                store=store,
                cancellation=cancellation,
            )
            if execution.run.state is not RunState.COMPLETE:
                raise ValueError(
                    f"{name}/{excitation} worker failed: {execution.run.diagnostics}; {execution.directory}"
                )
            runs.append(execution)
        executions[name] = tuple(runs)
    return executions


EVALUATOR_KEY = ("waffle.mmi.samples", "1")
_K0 = 2 * np.pi / _WAVELENGTH_M
_LOCALIZE = np.asarray(((1, -1), (1, 1))) / np.sqrt(2)
type _ComplexArray = NDArray[np.complex128]
type _RealArray = NDArray[np.float64]
type _ComparisonKind = Literal[
    "analytic_reference", "grid_convergence", "time_convergence", "boundary_convergence"
]


@dataclass(frozen=True)
class MmiCharacterization:
    evidence: ArtifactRef
    validation: ArtifactRef
    model_artifact: ArtifactRef | None
    model: ModelRevision | None
    mapping: ViewMapping | None
    report: ValidationReport
    executions: dict[str, tuple[ExecutionResult, ...]]


@dataclass(frozen=True)
class _LeadModes:
    core: float
    clad: float
    indices: tuple[float, float]

    def _profiles(self, y: _RealArray) -> _RealArray:
        # Integrate inwards from the upper guide's outer edge, with unit field
        # and derivative -gamma there. Continue by parity at the midplane.
        r = abs(y)
        values = []
        for order, index in enumerate(self.indices):
            gamma = _K0 * np.sqrt(index**2 - self.clad**2)
            k = _K0 * np.sqrt(self.core**2 - index**2)
            width = 0.5e-6
            depth = np.clip(0.9e-6 - r, 0.0, width)
            core = np.cos(k * depth) + gamma / k * np.sin(k * depth)
            gap = core * (
                np.cosh(gamma * r) / np.cosh(gamma * 0.4e-6)
                if order == 0
                else np.sinh(gamma * r) / np.sinh(gamma * 0.4e-6)
            )
            tail = np.exp(-gamma * np.maximum(r - 0.9e-6, 0.0))
            profile = np.where(r >= 0.9e-6, tail, np.where(r >= 0.4e-6, core, gap))
            values.append(profile if order == 0 else profile * np.sign(y))
        return np.stack(values, axis=1)

    def profiles(self, y: _RealArray) -> _RealArray:
        # Normalize both modes to the same full transverse L2 norm. Truncating
        # a measurement window must never change the definition of the basis.
        grid = np.linspace(-4e-6, 4e-6, 4001)
        norm = np.linalg.norm(self._profiles(grid), axis=0)
        return self._profiles(y) / norm


def _lead_modes(d: Reduced2D) -> _LeadModes:
    core, clad = d.regions[0].index, d.background_index
    layers = (SlabLayer(0.5e-6, core), SlabLayer(0.8e-6, clad), SlabLayer(0.5e-6, core))
    stack = SlabStack(clad, layers, clad)
    even, odd, extra = (
        effective_index(stack, _WAVELENGTH_M, Polarization.TE, order)
        for order in range(3)
    )
    if even is None or odd is None or extra is not None:
        raise ValueError(
            "reference access pair must guide exactly two lateral Ez supermodes"
        )
    return _LeadModes(core, clad, (even, odd))


def _fit(
    positions: _RealArray,
    samples: _ComplexArray,
    index: float,
) -> tuple[_ComplexArray, float, float]:
    """Return +x and -x amplitudes evaluated at the original port plane."""
    scale = float(np.linalg.norm(samples))
    if not np.isfinite(scale) or scale == 0:
        raise ValueError("zero or nonfinite coherent mode in lead")
    best = None
    for trial in np.linspace(0.97 * index, 1.03 * index, 241):
        wave = np.exp(1j * _K0 * trial * positions)
        basis = np.stack((wave, wave.conj()), axis=1)
        coefficients = np.linalg.lstsq(basis, samples, rcond=None)[0]
        residual = float(np.linalg.norm(basis @ coefficients - samples) / scale)
        if best is None or residual < best[1]:
            best = (coefficients, residual, float(trial))
    assert best is not None
    return best


@dataclass(frozen=True)
class _LeadFit:
    forward: _ComplexArray
    backward: _ComplexArray
    residuals: tuple[float, ...]
    indices: tuple[float, ...]
    cutoff: bool


def _fit_lead(
    execution: ExecutionResult,
    side: str,
    reference_m: float,
    modes: _LeadModes,
    window_m: float,
) -> _LeadFit:
    if execution.effective.get("time_convention") != "exp(-i omega t)":
        raise ValueError("worker did not report the expected coherent time convention")
    acquisitions = {a.name: a for a in execution.run.acquisitions}
    positions, projected = [], []
    for i in range(len(_OFFSETS)):
        name = f"{side}{i}"
        monitor = execution.effective["monitors"][name]
        coordinates = np.asarray(monitor["sample_coordinates_m"], dtype=float)
        position = float(monitor["plane_coordinate_m"])
        acquisition = acquisitions[name]
        if (
            monitor["normal_axis"] != "x"
            or monitor["field_component"] != "Ez"
            or acquisition.axes != ("frequency", "transverse")
            or acquisition.shape != (1, len(coordinates))
            or not np.isclose(monitor["frequencies_hz"][0], _FREQUENCY, rtol=1e-12)
            or not np.allclose(coordinates[:, 0], position, rtol=0, atol=1e-15)
            or not np.allclose(
                np.diff(coordinates[:, 1]),
                execution.effective["dx_m"],
                rtol=0,
                atol=1e-15,
            )
            or (side == "left" and position >= reference_m)
            or (side == "right" and position <= reference_m)
        ):
            raise ValueError(
                "expected uniformly sampled scalar Ez profiles at exterior x planes"
            )
        mask = abs(coordinates[:, 1]) <= window_m
        basis = modes.profiles(coordinates[mask, 1])
        field = np.asarray(acquisition.payload)[0, mask]
        projected.append(np.linalg.lstsq(basis, field, rcond=None)[0])
        positions.append(position - reference_m)
    if len(set(positions)) != len(_OFFSETS):
        raise ValueError("standing-wave fit needs distinct actual longitudinal planes")
    samples = np.asarray(projected)
    fits = tuple(
        _fit(np.asarray(positions), samples[:, order], index)
        for order, index in enumerate(modes.indices)
    )
    # For scalar Ez, P_x is proportional to beta * integral |Ez|² dy.
    # The common dimensional factor cancels between the identical lead pairs.
    waves = _LOCALIZE @ np.stack(
        [amplitude * np.sqrt(index) for (amplitude, _, index) in fits],
    )
    return _LeadFit(
        waves[:, 0],
        waves[:, 1],
        tuple(fit[1] for fit in fits),
        tuple(fit[2] for fit in fits),
        any(
            abs(fit[2] / nominal - 1) >= 0.0299
            for fit, nominal in zip(fits, modes.indices)
        ),
    )


@dataclass(frozen=True)
class _Transfer:
    transmission: _ComplexArray
    reflection: _ComplexArray
    incident_condition: float
    mirror_source_error: float
    right_incoming: float
    maximum_residual: float
    search_limit: bool
    fitted_indices: tuple[tuple[float, ...], ...]


def _relative_norm(numerator: _ComplexArray, denominator: _ComplexArray) -> float:
    scale = float(np.linalg.norm(denominator))
    if scale == 0:
        raise ValueError("modal amplitude denominator is zero")
    return float(np.linalg.norm(numerator) / scale)


def _transfer(
    executions: tuple[ExecutionResult, ...],
    x0: float,
    x1: float,
    modes: _LeadModes,
    window_m: float,
) -> _Transfer:
    left = tuple(_fit_lead(run, "left", x0, modes, window_m) for run in executions)
    right = tuple(_fit_lead(run, "right", x1, modes, window_m) for run in executions)
    incident = np.stack([fit.forward for fit in left], axis=1)
    reflected = np.stack([fit.backward for fit in left], axis=1)
    transmitted = np.stack([fit.forward for fit in right], axis=1)
    right_incoming = np.stack([fit.backward for fit in right], axis=1)
    condition = float(np.linalg.cond(incident))
    if not np.isfinite(condition):
        raise ValueError("incident launch matrix is singular")
    # Two independent point sources allow removal of unintended incidence in
    # the neighboring guide. Inversion uses the actual incident modal waves.
    inverse = np.linalg.inv(incident)
    # Compare independently acquired mirror experiments before forming S.
    first = np.concatenate((incident[:, 0], reflected[:, 0], transmitted[:, 0]))
    second = np.concatenate(
        (incident[::-1, 1], reflected[::-1, 1], transmitted[::-1, 1])
    )
    first, second = first / incident[0, 0], second / incident[1, 1]
    if not np.all(np.isfinite((first, second))):
        raise ValueError("primary incident waves are unresolvable")
    return _Transfer(
        transmitted @ inverse,
        reflected @ inverse,
        condition,
        _relative_norm(first - second, first),
        _relative_norm(right_incoming, transmitted),
        max(r for fit in (*left, *right) for r in fit.residuals),
        any(fit.cutoff for fit in (*left, *right)),
        tuple(fit.indices for fit in (*left, *right)),
    )


def _checks(transfers: dict[str, _Transfer]) -> tuple[Comparison, ...]:
    fine = transfers["fine"]
    records: list[Comparison] = []
    for name, transfer in transfers.items():
        for quantity, error, tolerance in (
            ("incident_condition_number", transfer.incident_condition, 2.0),
            (
                "mirror_launch_complex_relative_error",
                transfer.mirror_source_error,
                0.05,
            ),
            ("right_boundary_incident_fraction", transfer.right_incoming, 0.05),
            ("standing_wave_fit_residual", transfer.maximum_residual, 0.05),
            ("modal_index_search_at_limit", float(transfer.search_limit), 0.0),
        ):
            records.append(
                Comparison(
                    quantity=f"{name}.{quantity}",
                    frequency_hz=_FREQUENCY,
                    error=error,
                    tolerance=tolerance,
                    unit="1",
                    comparison="analytic_reference",
                )
            )
    # Bound measured guided power leaving for o1 incidence. Radiation can
    # make the guided sum less than one, so only excess power is an error.
    power = float(
        np.sum(abs(fine.transmission[:, 0]) ** 2)
        + np.sum(abs(fine.reflection[:, 0]) ** 2)
    )
    records.append(
        Comparison(
            quantity="guided_column_power_excess",
            frequency_hz=_FREQUENCY,
            error=max(power - 1, 0),
            tolerance=0.05,
            unit="1",
            comparison="analytic_reference",
        )
    )
    cases: tuple[tuple[str, _ComparisonKind, float, float], ...] = (
        ("coarse", "grid_convergence", 0.05, 0.15),
        ("time", "time_convergence", 0.05, 0.15),
        ("boundary", "boundary_convergence", 0.05, 0.15),
        ("window", "analytic_reference", 0.01, 0.02),
    )
    for name, kind, magnitude, phase in cases:
        for (output, excitation), row in zip(_OBSERVED, (1, 0)):
            records.extend(
                check.model_copy(
                    update={
                        "quantity": f"{name}.{output}/{excitation}.{check.quantity}"
                    }
                )
                for check in compare_complex(
                    complex(fine.transmission[row, 0]),
                    complex(transfers[name].transmission[row, 0]),
                    _FREQUENCY,
                    magnitude_tolerance=magnitude,
                    phase_tolerance_rad=phase,
                    comparison=kind,
                )
            )
    for (output, excitation), row in zip(_OBSERVED, (1, 0)):
        records.extend(
            check.model_copy(
                update={"quantity": f"mirror.{output}/{excitation}.{check.quantity}"}
            )
            for check in compare_complex(
                complex(fine.transmission[row, 0]),
                complex(fine.transmission[1 - row, 1]),
                _FREQUENCY,
                magnitude_tolerance=0.05,
                phase_tolerance_rad=0.15,
                comparison="analytic_reference",
            )
        )
    return tuple(records)


def characterize_mmi(
    problem: PhysicalProblem,
    template: ModelRevision,
    mapping: ViewMapping,
    *,
    command: tuple[str, ...],
    root: Path,
    store: ArtifactStore,
    cancellation: CancellationToken | None = None,
) -> MmiCharacterization:
    """Acquire one exact 2D TM sample and publish a two-element model if qualified."""
    d, x0, x1 = _reference(problem)
    ports = {port.path: port for port in d.ports}
    if (
        mapping.problem != revision_of(problem)
        or mapping.layout != problem.layout
        or mapping.model != revision_of(template)
        or mapping.interface != template.interface
        or problem.design not in template.designs
        or len(mapping.bindings) != len(_CHANNELS)
        or {item.model_channel for item in mapping.bindings} != set(_CHANNELS)
        or any(
            item.terminal != item.model_channel
            or item.layout_port != item.model_channel
            or item.simulation_port != item.model_channel
            or item.reference_plane_offset_dbu != 0
            or item.mode != 0
            or item.modal_transform is not ModalTransform.IDENTITY
            or item.normalization is not Normalization.POWER_WAVE
            or item.anchor.center
            != mapping.transform.apply(ports[item.simulation_port].center)
            or item.anchor.orientation
            != mapping.transform.apply_direction(
                ports[item.simulation_port].orientation
            )
            for item in mapping.bindings
        )
    ):
        raise ValueError(
            "MMI recipe requires matching template, design, and original modal mapping"
        )
    modes = _lead_modes(d)
    runs = _acquire(
        problem, command=command, root=root, store=store, cancellation=cancellation
    )
    return _publish(problem, template, mapping, store, runs, modes, x0, x1)


def _publish(
    problem: PhysicalProblem,
    template: ModelRevision,
    mapping: ViewMapping,
    store: ArtifactStore,
    runs: dict[str, tuple[ExecutionResult, ...]],
    modes: _LeadModes,
    x0: int,
    x1: int,
) -> MmiCharacterization:
    inputs = (
        InputLink(role="seed_problem", target=revision_of(problem)),
        InputLink(role="template", target=revision_of(template)),
        InputLink(role="template_mapping", target=revision_of(mapping)),
        *(
            InputLink(role="simulation", target=run.artifact)
            for series in runs.values()
            for run in series
            if run.artifact is not None
        ),
    )
    evidence = {
        "procedure": "mmi_ez_supermode_transfer",
        "version": "1",
        "frequency_hz": _FREQUENCY,
        "wavelength_pm": _WAVELENGTH_PM,
        "time_convention": "exp(-i omega t)",
        "reference_planes_m": {
            name: problem.metres(x0 if name in ("o1", "o2") else x1)
            for name in _CHANNELS
        },
        "normalization": "even_odd_Ez_projection_power_scaled_localized_basis",
        "analytic_supermode_indices": list(modes.indices),
        "observed": [],
    }
    extraction_error = None
    try:
        transfers = {
            name: _transfer(
                series, problem.metres(x0), problem.metres(x1), modes, window
            )
            for name, series, window in (
                *((name, series, 1.65e-6) for name, series in runs.items()),
                ("window", runs["fine"], 1.3e-6),
            )
        }
        comparisons = _checks(transfers)
        evidence["observed"] = [
            {
                "output": output,
                "excitation": excitation,
                "real": float(transfers["fine"].transmission[row, 0].real),
                "imag": float(transfers["fine"].transmission[row, 0].imag),
            }
            for (output, excitation), row in zip(_OBSERVED, (1, 0))
        ]
        evidence["fitted_indices"] = {
            name: transfer.fitted_indices for name, transfer in transfers.items()
        }
        evidence["guided_column_power"] = float(
            np.sum(abs(transfers["fine"].transmission[:, 0]) ** 2)
            + np.sum(abs(transfers["fine"].reflection[:, 0]) ** 2)
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        extraction_error = str(exc)
        evidence["diagnostic"] = {
            "severity": "error",
            "code": "mmi.modal_extraction_failed",
            "message": extraction_error,
        }
        comparisons = (
            Comparison(
                quantity="modal_extraction_failed",
                frequency_hz=_FREQUENCY,
                error=1.0,
                tolerance=0.0,
                unit="1",
                comparison="analytic_reference",
            ),
        )
    observed = _OBSERVED if extraction_error is None else ()
    report = ValidationReport(
        id="validation_report:mmi",
        procedure="mmi_ez_supermode_transfer",
        procedure_version="1",
        reference_description=(
            "Independent mirrored point launches against lateral reflection symmetry of the exact layout. "
            "Analytic symmetric double-slab lateral Ez eigenmodes and power normalization."
        ),
        inputs=tuple(link.target for link in inputs),
        comparisons=comparisons,
        observed_channels=observed,
        uncomputed_channels=tuple(
            (out, exc)
            for out in _CHANNELS
            for exc in _CHANNELS
            if (out, exc) not in observed
        ),
        limitations=(
            "2D effective-index approximation at one exact 1550 nm TM sample. No interpolation.",
            "Analysis-only uniform coupled-strip extensions reach the longitudinal PML. Fits are translated to the original taper ends.",
            "Localized port waves are defined as equal combinations of the power-scaled even and odd guided lead modes.",
            "Reflection and second launch estimates are internal diagnostics. Available channels cover only two forward responses for o1 incidence.",
            "The guided power budget leaves radiation unmodeled. Two-grid, time and PML comparisons do not estimate statistical uncertainty.",
        )
        + (
            (f"Modal extraction diagnostic: {extraction_error}",)
            if extraction_error
            else ()
        ),
    )
    operation = OperationInfo(
        name="mmi.characterize",
        version="1",
        parameters_digest=content_hash(
            {"transfer": evidence, "report": report.model_dump(mode="json")}
        ),
    )
    with store.attempt(operation, inputs) as attempt:
        writer = attempt.new_artifact("mmi_transfer")
        writer.write_json("transfer.json", evidence)
        raw_ref = writer.commit()
        writer = attempt.new_artifact("validation_report")
        writer.write_json("report.json", report.model_dump(mode="json"))
        report_ref = writer.commit()
    if not report.passed:
        return MmiCharacterization(raw_ref, report_ref, None, None, None, report, runs)
    model = template.model_copy(
        update={
            "payload": EvaluatorPayload(
                name=EVALUATOR_KEY[0], version=EVALUATOR_KEY[1]
            ),
            "capability": OpticalScattering(observed_channels=_OBSERVED),
            "applicability_spec": Applicability(
                axes=(
                    AxisCoverage(
                        parameter="frequency",
                        sampled=(Quantity(value=_FREQUENCY, unit="Hz"),),
                    ),
                )
            ),
            "evidence": (
                *(
                    EvidenceRef(kind=EvidenceKind.SIMULATION, ref=link.target)
                    for link in inputs
                    if isinstance(link.target, ArtifactRef)
                ),
                EvidenceRef(kind=EvidenceKind.SIMULATION, ref=raw_ref),
                EvidenceRef(kind=EvidenceKind.VALIDATION, ref=report_ref),
            ),
            "metadata": {
                "device": "mmi2x2",
                "polarization": "tm",
                "extraction": "mmi_ez_supermode_transfer_v1",
                "port_basis": str(evidence["normalization"]),
            },
        }
    )
    selected_mapping = mapping.model_copy(
        update={
            "model": revision_of(model),
            "responses": tuple(
                ResponseChannel(
                    output=out,
                    excitation=exc,
                    status=ResponseStatus.COMPUTED
                    if (out, exc) in _OBSERVED
                    else ResponseStatus.UNCOMPUTED,
                )
                for out in _CHANNELS
                for exc in _CHANNELS
            ),
        }
    )
    with store.attempt(
        operation,
        (
            InputLink(role="simulation", target=raw_ref),
            InputLink(role="validation", target=report_ref),
        ),
    ) as attempt:
        writer = attempt.new_artifact("model_revision")
        writer.write_json("model.json", model.model_dump(mode="json"))
        model_ref = writer.commit()
    return MmiCharacterization(
        raw_ref, report_ref, model_ref, model, selected_mapping, report, runs
    )


def sample_evaluator(store: ArtifactStore, model: ModelRevision) -> OpticalEvaluator:
    """Restore the qualified exact-sample MMI evaluator."""
    refs = [
        item.ref
        for item in model.evidence
        if item.kind is EvidenceKind.SIMULATION
        and isinstance(item.ref, ArtifactRef)
        and item.ref.kind == "mmi_transfer"
    ]
    try:
        if (
            len(refs) != 1
            or not isinstance(model.payload, EvaluatorPayload)
            or (model.payload.name, model.payload.version) != EVALUATOR_KEY
        ):
            raise ValueError(
                "model needs one MMI transfer artifact and the pinned evaluator"
            )
        evidence = json.loads(store.read_bytes(refs[0], "transfer.json"))
        samples = {
            (item["output"], item["excitation"]): complex(item["real"], item["imag"])
            for item in evidence["observed"]
        }
        if (
            evidence["procedure"] != "mmi_ez_supermode_transfer"
            or evidence["version"] != "1"
            or evidence["frequency_hz"] != _FREQUENCY
            or set(samples) != set(_OBSERVED)
            or any(not np.isfinite(value) for value in samples.values())
        ):
            raise ValueError(
                "transfer evidence does not match the qualified MMI procedure"
            )
    except (ValueError, KeyError, TypeError) as exc:
        raise ArtifactError(
            Diagnostic(
                severity=Severity.ERROR,
                code="mmi.evidence_invalid",
                message=str(exc),
            )
        ) from exc
    pinned = revision_of(model)

    def evaluate(
        selected: ModelRevision,
        operating: OperatingValues,
        requested: frozenset[ChannelPair],
    ) -> Mapping[ChannelPair, complex]:
        if revision_of(selected) != pinned:
            raise ValueError("sample evaluator used with a different model revision")
        if (
            operating.values["frequency"].si != _FREQUENCY
            or not requested <= samples.keys()
        ):
            return {}
        return {pair: samples[pair] for pair in requested}

    return evaluate
