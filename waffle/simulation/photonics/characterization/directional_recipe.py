"""Acquire and qualify the bounded 1550 nm TM straight-guide benchmark."""

from __future__ import annotations

import cmath
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from waffle.artifacts.store import ArtifactStore
from waffle.characterization.validation.compare import compare_complex
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
)
from waffle.contracts.characterization.report import Comparison, ValidationReport
from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.physics.approximations import (
    EffectiveIndexReduction,
    Polarization,
)
from waffle.contracts.physics.problem import (
    Boundaries2D,
    BoundaryCondition,
    BoundaryKind,
    Domain2D,
    PhysicalProblem,
    PortPlane,
    Reduced2D,
)
from waffle.execution.worker import ExecutionResult, run_isolated
from waffle.simulation.common.backend import CancellationToken, RunState
from waffle.simulation.photonics.characterization.directional import (
    DirectionalFit,
    TwoPortEstimate,
    estimate_two_port,
    fit_directional_waves,
)
from waffle.simulation.photonics.device_analysis.slab import (
    SlabLayer,
    SlabStack,
    effective_index,
)

WAVELENGTH_PM = 1_550_000
WAVELENGTH_M = WAVELENGTH_PM * 1e-12
FREQUENCY_HZ = 299_792_458.0 / WAVELENGTH_M
K0 = 2 * np.pi / WAVELENGTH_M
REFERENCES_DBU = (0, 2000)
HALF_WIDTH_DBU = 250
LEFT_OFFSETS_DBU = (-1500, -1250, -975, -650, -400, -175, 75, 300)
DOMAIN_LO, DOMAIN_HI = (-10_000, -3000), (12_000, 3000)
MONITOR_WIDTH_DBU = 3000
TIME_STEP_AS = 25
BEAMZ_VERSION = "0.6.2"
SOURCE_WIDTH_PM = 100_000
PULSE_CENTER_AS = 40_000
PULSE_WIDTH_AS = 12_000
PORT_NAMES = ("o1", "o2")
CHANNELS = tuple(
    (output, excitation) for output in PORT_NAMES for excitation in PORT_NAMES
)


@dataclass(frozen=True)
class DirectionalBenchmark:
    """Numerical evidence and scattering coefficients keyed by (output, excitation)."""

    report: ValidationReport
    summary: dict[str, object]
    executions: tuple[ExecutionResult, ...]
    responses: dict[tuple[str, str], complex]


@dataclass(frozen=True)
class _Settings:
    name: str
    spacing_pm: int
    steps: int
    pml_pm: int


SETTINGS = (
    _Settings("fine", 12_500, 12_000, 1_500_000),
    _Settings("medium", 20_000, 12_000, 1_500_000),
    _Settings("coarse", 25_000, 12_000, 1_500_000),
    _Settings("duration", 12_500, 8_000, 1_500_000),
    _Settings("boundary", 12_500, 12_000, 1_000_000),
)


@dataclass(frozen=True)
class _MonitorSample:
    x_m: float
    y_m: NDArray[np.float64]
    values: NDArray[np.complexfloating]


def _validate_problem(problem: PhysicalProblem) -> Reduced2D:
    description = problem.description
    if not isinstance(description, Reduced2D) or problem.dbu_pm != 1000:
        raise ValueError(
            "directional benchmark requires a 2D reduced problem with 1 nm dbu"
        )
    x0, x1 = REFERENCES_DBU
    y0, y1 = -HALF_WIDTH_DBU, HALF_WIDTH_DBU
    rectangle = Polygon(points=((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    if len(description.regions) != 1 or description.regions[0].polygons != (rectangle,):
        raise ValueError(
            "directional benchmark requires one 2 µm by 500 nm rectangle from (0, -250) to (2000, 250) dbu"
        )

    region = description.regions[0]
    expected_ports = tuple(
        PortPlane(
            path=name,
            layer=region.layer,
            domain=Domain.OPTICAL,
            center=(plane, 0),
            width=2 * HALF_WIDTH_DBU,
            orientation=orientation,
        )
        for name, plane, orientation in zip(
            PORT_NAMES, REFERENCES_DBU, (180, 0), strict=True
        )
    )
    if tuple(sorted(description.ports, key=lambda port: port.path)) != expected_ports:
        raise ValueError(
            "directional benchmark requires 500 nm optical ports o1 and o2 centered on the rectangle ends with outward normals"
        )
    domain = description.domain
    if (
        domain.lo[0] > x0
        or domain.hi[0] < x1
        or domain.lo[1] > y0
        or domain.hi[1] < y1
        or any(
            boundary.kind is not BoundaryKind.ABSORBING
            for _, boundary in domain.boundaries
        )
    ):
        raise ValueError(
            "directional benchmark requires an absorbing seed domain containing the entire rectangle"
        )

    if len(problem.approximations) != 1:
        raise ValueError(
            "directional benchmark requires exactly one effective-index reduction"
        )
    (reduction,) = problem.approximations
    if (
        not isinstance(reduction, EffectiveIndexReduction)
        or reduction.polarization is not Polarization.TM
        or reduction.wavelength_pm != WAVELENGTH_PM
        or reduction.mode_order != 0
        or (reduction.method, reduction.method_version) != ("slab_transfer_matrix", 1)
    ):
        raise ValueError(
            "directional benchmark requires a 1550 nm TM0 slab_transfer_matrix version 1 reduction"
        )
    core, cladding = reduction.column(region.layer), reduction.column(None)
    if (
        len(reduction.columns) != 2
        or core is None
        or cladding is None
        or (region.index, description.background_index) != (2.0533196788, 1.444)
        or (core.n_eff, cladding.n_eff) != (region.index, description.background_index)
        or (core.rule, cladding.rule) != ("guided_mode", "cladding_cutoff")
        or len(cladding.segments) != 1
        or len(core.segments) != 3
    ):
        raise ValueError(
            "directional benchmark requires the reference lossless silicon/silica reduced indices and columns"
        )
    below, silicon, above = core.segments
    (background,) = cladding.segments
    if (
        (below.index, silicon.index, above.index, background.index)
        != (1.444, 3.476, 1.444, 1.444)
        or silicon.z_max_pm - silicon.z_min_pm != 220_000
        or (below.z_max_pm, above.z_min_pm) != (silicon.z_min_pm, silicon.z_max_pm)
        or (below.z_min_pm, above.z_max_pm)
        != (background.z_min_pm, background.z_max_pm)
        or (background.z_min_pm, background.z_max_pm)
        != (reduction.window_z_min_pm, reduction.window_z_max_pm)
    ):
        raise ValueError(
            "directional benchmark requires a 220 nm silicon layer between silica claddings"
        )
    return description


def _extend_problem(
    seed: PhysicalProblem,
    description: Reduced2D,
    settings: _Settings,
) -> PhysicalProblem:
    (region,) = description.regions
    lo, hi = DOMAIN_LO, DOMAIN_HI
    y0, y1 = -HALF_WIDTH_DBU, HALF_WIDTH_DBU
    extension = Polygon(points=((lo[0], y0), (hi[0], y0), (hi[0], y1), (lo[0], y1)))
    boundary = BoundaryCondition(thickness_pm=settings.pml_pm)
    ports = tuple(
        PortPlane(
            path=f"{side}{index}",
            layer=region.layer,
            domain=Domain.OPTICAL,
            center=(offset + plane, 0),
            width=MONITOR_WIDTH_DBU,
            orientation=0,
        )
        for side, plane, offsets in (
            ("left", REFERENCES_DBU[0], LEFT_OFFSETS_DBU),
            (
                "right",
                REFERENCES_DBU[1],
                tuple(-offset for offset in reversed(LEFT_OFFSETS_DBU)),
            ),
        )
        for index, offset in enumerate(offsets)
    )
    return seed.model_copy(
        update={
            "id": EntityId("physical_problem", f"directional_{settings.name}"),
            "metadata": {
                **seed.metadata,
                "seed_problem": str(revision_of(seed)),
                "analysis_extension": "identical 500 nm access leads extended through both x PMLs",
            },
            "description": description.model_copy(
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
                    "ports": ports,
                    "regions": (region.model_copy(update={"polygons": (extension,)}),),
                }
            ),
        }
    )


def _request(
    problem: PhysicalProblem,
    settings: _Settings,
    source_x: int,
    side: str,
) -> AnalysisRequest:
    return AnalysisRequest(
        id=EntityId("analysis_request", f"directional_{settings.name}_{side}"),
        problem=revision_of(problem),
        polarization=Polarization.TM,
        excitations=(
            Excitation(
                kind="point", center_dbu=(source_x, 0), wavelength_pm=WAVELENGTH_PM
            ),
        ),
        observables=tuple(
            Observable(
                name=port.path, kind="raw_field", field_component="Ez", port=port.path
            )
            for port in problem.description.ports
        ),
        numerical=NumericalSettings(
            grid_spacing_pm=settings.spacing_pm,
            time_step_as=TIME_STEP_AS,
            steps=settings.steps,
        ),
        backend_settings=BackendSettings(
            backend="beamz",
            version=BEAMZ_VERSION,
            schema_version=1,
            parameters={
                "source_width_pm": SOURCE_WIDTH_PM,
                "pulse_center_as": PULSE_CENTER_AS,
                "pulse_width_as": PULSE_WIDTH_AS,
            },
        ),
    )


def _sample_monitors(
    run: ExecutionResult,
    problem: PhysicalProblem,
    request: AnalysisRequest,
) -> tuple[_MonitorSample, ...]:
    metadata = run.effective
    dx = request.numerical.grid_spacing_pm * 1e-12
    fingerprint = content_hash(
        {
            "problem": problem.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
        }
    )
    expected = {
        "schema_version": 1,
        "input_fingerprint": fingerprint,
        "beamz": BEAMZ_VERSION,
        "dx_m": dx,
        "dt_s": TIME_STEP_AS * 1e-18,
        "steps": request.numerical.steps,
        "frequency_hz": FREQUENCY_HZ,
        "source_width_m": SOURCE_WIDTH_PM * 1e-12,
        "pulse_center_s": PULSE_CENTER_AS * 1e-18,
        "pulse_width_s": PULSE_WIDTH_AS * 1e-18,
        "time_convention": "exp(-i omega t)",
        "dft_kernel": "exp(+i omega t)",
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(
                f"worker metadata {key} differs from the pinned directional settings"
            )
    ports = problem.description.ports
    monitors = metadata.get("monitors")
    if not isinstance(monitors, dict) or monitors.keys() != {
        port.path for port in ports
    }:
        raise ValueError("worker monitor metadata does not cover all requested planes")

    acquisitions = {item.name: item for item in run.run.acquisitions}
    transverse_count = (
        MONITOR_WIDTH_DBU * problem.dbu_pm // request.numerical.grid_spacing_pm + 1
    )
    expected_y = np.arange(transverse_count) * dx - problem.metres(
        MONITOR_WIDTH_DBU // 2
    )
    origin_x = problem.metres(DOMAIN_LO[0])
    coordinate_tolerance_m = 1e-15
    samples = []
    for port in ports:
        name = port.path
        monitor = monitors[name]
        if (
            not isinstance(monitor, dict)
            or monitor.get("port") != name
            or monitor.get("normal_axis") != "x"
            or monitor.get("orientation") != 0
            or monitor.get("field_component") != "Ez"
            or monitor.get("sampling") != "public_get_grid_points_2d_direct_Yee_support"
            or monitor.get("dft_scaling")
            != "public_get_dft_component_temporal_average_no_source_normalization"
            or monitor.get("dft_weight_sum") != [request.numerical.steps]
            or monitor.get("dft_amplitude_scale") != 2.0
        ):
            raise ValueError(
                f"worker monitor {name} does not verify coherent Ez sampling"
            )
        try:
            x = float(monitor["plane_coordinate_m"])
            coords = np.asarray(monitor["sample_coordinates_m"], dtype=np.float64)
            frequencies = np.asarray(monitor["frequencies_hz"], dtype=np.float64)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"worker monitor {name} has invalid sampling coordinates or frequencies"
            ) from exc
        if frequencies.shape != (1,) or not np.allclose(
            frequencies, [FREQUENCY_HZ], rtol=1e-12, atol=0
        ):
            raise ValueError(
                f"worker monitor {name} does not sample the benchmark frequency"
            )
        target_x = problem.metres(port.center[0])
        if (
            not np.isfinite(x)
            or abs(x - target_x) > dx / 2 + coordinate_tolerance_m
            or abs(x - (origin_x + round((x - origin_x) / dx) * dx))
            > coordinate_tolerance_m
            or coords.shape != (transverse_count, 2)
            or not np.allclose(coords[:, 0], x, rtol=0, atol=coordinate_tolerance_m)
            or not np.allclose(
                coords[:, 1], expected_y, rtol=0, atol=coordinate_tolerance_m
            )
        ):
            raise ValueError(
                f"worker monitor {name} samples a shifted or irregular plane"
            )
        acquisition = acquisitions[name]
        payload = np.asarray(acquisition.payload)
        if (
            acquisition.axes != ("frequency", "transverse")
            or acquisition.shape != (1, transverse_count)
            or not np.iscomplexobj(payload)
        ):
            raise ValueError(
                f"worker monitor {name} must provide one complex transverse profile"
            )
        samples.append(_MonitorSample(x, coords[:, 1], payload[0]))
    return tuple(samples)


def _fit(
    samples: tuple[_MonitorSample, ...],
    core: float,
    clad: float,
    nref: float,
    half_window_m: float,
) -> DirectionalFit:
    k = K0 * np.sqrt(core**2 - nref**2)
    gamma = K0 * np.sqrt(nref**2 - clad**2)
    half_width_m = HALF_WIDTH_DBU / 1e9
    planes = np.repeat(np.asarray(REFERENCES_DBU) * 1e-9, len(LEFT_OFFSETS_DBU))
    offsets, projections = [], []
    for plane, sample in zip(planes, samples, strict=True):
        cut = abs(sample.y_m) <= half_window_m
        y = abs(sample.y_m[cut])
        mode = np.where(
            y <= half_width_m,
            np.cos(k * y),
            np.cos(k * half_width_m) * np.exp(-gamma * (y - half_width_m)),
        )
        offsets.append(sample.x_m - plane)
        projections.append(np.dot(mode, sample.values[cut]) / np.dot(mode, mode))
    return fit_directional_waves(
        np.asarray(offsets).reshape(2, -1),
        np.asarray(projections).reshape(2, -1),
        K0 * nref,
    ).unwrap()


def _comparisons(
    fits: dict[str, tuple[DirectionalFit, DirectionalFit]],
    estimates: dict[str, TwoPortEstimate],
    reference: complex,
) -> tuple[Comparison, ...]:
    fine = estimates["fine"].scattering
    comparisons: list[Comparison] = []

    def check(quantity: str, error: float, tolerance: float) -> None:
        comparisons.append(
            Comparison(
                quantity=quantity,
                frequency_hz=FREQUENCY_HZ,
                error=error,
                tolerance=tolerance,
                unit="1",
                comparison="analytic_reference",
            )
        )

    for name, estimate in estimates.items():
        matrix = estimate.scattering
        fit = fits[name]
        check(
            f"{name}.relative_fit_residual",
            max(w.relative_residual for run in fit for w in (run.left, run.right)),
            0.02,
        )
        check(
            f"{name}.power_norm_excess",
            max(0.0, float(np.linalg.norm(matrix, ord=2) ** 2 - 1)),
            0.001,
        )
        check(f"{name}.reciprocity_error", abs(matrix[0, 1] - matrix[1, 0]), 0.005)
        check(
            f"{name}.reflection_magnitude",
            max(abs(matrix[0, 0]), abs(matrix[1, 1])),
            0.01,
        )
    for row, column in ((0, 1), (1, 0)):
        for name, target, method, magnitude_tol, phase_tol in (
            ("slab", reference, "analytic_reference", 0.005, 0.035),
            (
                "coarse",
                estimates["coarse"].scattering[row, column],
                "grid_convergence",
                0.004,
                0.03,
            ),
            (
                "medium",
                estimates["medium"].scattering[row, column],
                "grid_convergence",
                0.004,
                0.02,
            ),
            (
                "duration",
                estimates["duration"].scattering[row, column],
                "time_convergence",
                0.004,
                0.015,
            ),
            (
                "boundary",
                estimates["boundary"].scattering[row, column],
                "boundary_convergence",
                0.004,
                0.015,
            ),
            (
                "window",
                estimates["window"].scattering[row, column],
                "analytic_reference",
                0.002,
                0.01,
            ),
        ):
            comparisons.extend(
                comparison.model_copy(
                    update={
                        "quantity": f"{name}.s{row + 1}{column + 1}.{comparison.quantity}"
                    }
                )
                for comparison in compare_complex(
                    complex(fine[row, column]),
                    complex(target),
                    FREQUENCY_HZ,
                    magnitude_tolerance=magnitude_tol,
                    phase_tolerance_rad=phase_tol,
                    comparison=method,
                )
            )
    return tuple(comparisons)


def _total_field_ratio(fit: DirectionalFit) -> float | None:
    left = fit.left.positive_x + fit.left.negative_x
    if left == 0:
        return None
    magnitude = abs((fit.right.positive_x + fit.right.negative_x) / left)
    return magnitude if np.isfinite(magnitude) else None


def _summary(
    runs: dict[str, tuple[ExecutionResult, ExecutionResult]],
    fits: dict[str, tuple[DirectionalFit, DirectionalFit]],
    estimates: dict[str, TwoPortEstimate],
    report: ValidationReport,
    nref: float,
    reference: complex,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "frequency_hz": FREQUENCY_HZ,
        "analytic_index": nref,
        "analytic_transmission": [reference.real, reference.imag],
        "settings": {
            s.name: {
                "spacing_pm": s.spacing_pm,
                "time_step_as": TIME_STEP_AS,
                "steps": s.steps,
                "pml_pm": s.pml_pm,
            }
            for s in SETTINGS
        },
        "runs": {
            name: [
                {"artifact": str(run.artifact), "attempt_id": run.attempt_id}
                for run in pair
            ]
            for name, pair in runs.items()
        },
        "estimates": {
            name: {
                "matrix": [
                    [[float(value.real), float(value.imag)] for value in row]
                    for row in estimate.scattering
                ],
                "incident_condition": estimate.incident_condition,
                "fitted_indices": [float(f.beta_rad_m / K0) for f in fits[name]],
                "left_launch_total_field_ratio_magnitude": _total_field_ratio(
                    fits[name][0]
                ),
            }
            for name, estimate in estimates.items()
        },
        "report": report.model_dump(mode="json"),
    }


def run_directional_benchmark(
    problem: PhysicalProblem,
    *,
    command: tuple[str, ...],
    root: Path,
    store: ArtifactStore,
    cancellation: CancellationToken | None = None,
) -> DirectionalBenchmark:
    """Run both launches at each pinned setting and return the 48 qualification checks.

    Worker attempts and completed raw artifacts remain in the store on failure or
    cancellation. A ValueError identifies the failed setting and retained worker
    directory. Failed numerical checks are returned in the report with the estimates.
    """
    description = _validate_problem(problem)
    core = description.regions[0].index
    clad = description.background_index
    stack = SlabStack(
        clad,
        (SlabLayer(problem.metres(2 * HALF_WIDTH_DBU), core),),
        clad,
    )
    # The vertical TM reduction uses Ez, which has TE continuity in the lateral slab.
    nref = effective_index(stack, WAVELENGTH_M, Polarization.TE)
    if (
        nref is None
        or effective_index(stack, WAVELENGTH_M, Polarization.TE, 1) is not None
    ):
        raise ValueError(
            "directional benchmark requires exactly one lateral guided mode"
        )
    reference = cmath.exp(
        1j * K0 * nref * problem.metres(REFERENCES_DBU[1] - REFERENCES_DBU[0])
    )

    runs: dict[str, tuple[ExecutionResult, ExecutionResult]] = {}
    fits: dict[str, tuple[DirectionalFit, DirectionalFit]] = {}
    window_fits: list[DirectionalFit] = []
    for settings in SETTINGS:
        derived = _extend_problem(problem, description, settings)
        pair: list[ExecutionResult] = []
        fit_pair: list[DirectionalFit] = []
        for side, source_x in (("left", -8000), ("right", 10000)):
            request = _request(derived, settings, source_x, side)
            run = run_isolated(
                derived,
                request,
                command=command,
                root=root,
                store=store,
                cancellation=cancellation,
            )
            context = f"{settings.name} {side} launch, attempt {run.attempt_id} at {run.directory}"
            if run.run.state is not RunState.COMPLETE or run.artifact is None:
                detail = "\n".join(
                    str(diagnostic) for diagnostic in run.run.diagnostics
                )
                raise ValueError(
                    f"{context}: worker {run.run.state.value}. {detail}".rstrip()
                )
            try:
                samples = _sample_monitors(run, derived, request)
                fit_pair.append(_fit(samples, core, clad, nref, 1.5e-6))
                if settings.name == "fine":
                    window_fits.append(_fit(samples, core, clad, nref, 1.0e-6))
            except ValueError as exc:
                raise ValueError(f"{context}: {exc}") from exc
            pair.append(run)
        runs[settings.name] = pair[0], pair[1]
        fits[settings.name] = fit_pair[0], fit_pair[1]
    fits["window"] = window_fits[0], window_fits[1]

    estimates: dict[str, TwoPortEstimate] = {}
    for name, pair_fits in fits.items():
        try:
            estimates[name] = estimate_two_port(*pair_fits).unwrap()
        except ValueError as exc:
            directories = ", ".join(
                str(run.directory) for run in runs["fine" if name == "window" else name]
            )
            raise ValueError(f"{name} launches at {directories}: {exc}") from exc

    executions = tuple(run for pair in runs.values() for run in pair)
    if cancellation is not None and cancellation.cancelled():
        raise ValueError(
            f"directional benchmark cancelled; inspect worker attempts at {root.resolve()}"
        )
    report = ValidationReport(
        id="validation_report:directional_waveguide",
        procedure="uniform_guide_directional_two_port",
        procedure_version="1",
        inputs=(
            revision_of(problem),
            *(run.artifact for run in executions if run.artifact is not None),
        ),
        reference_description="Lossless symmetric single-mode lateral slab, equal power normalization at both ports, zero reflection",
        comparisons=_comparisons(fits, estimates, reference),
        observed_channels=CHANNELS,
        limitations=(
            "Only the 2D reduced-index 1550 nm TM benchmark at the pinned endpoint planes.",
            "Boundary sensitivity and residual modal/radiation contamination remain numerical error.",
            "Reflection phase is not compared to a zero analytic amplitude. No interpolation or experimental validation.",
        ),
    )
    fine = estimates["fine"].scattering
    return DirectionalBenchmark(
        report=report,
        summary=_summary(runs, fits, estimates, report, nref, reference),
        executions=executions,
        responses=dict(zip(CHANNELS, map(complex, fine.flat), strict=True)),
    )
