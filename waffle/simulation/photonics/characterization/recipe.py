"""Measured, bounded uniform-guide characterization through isolated workers."""

from __future__ import annotations

import cmath
from dataclasses import dataclass
from pathlib import Path

import numpy as np

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
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.mappings.mapping import (
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.model import ModelRevision
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import (
    Boundaries2D,
    BoundaryCondition,
    Domain2D,
    PhysicalProblem,
    PortPlane,
    Reduced2D,
)
from waffle.contracts.results.provenance import InputLink
from waffle.execution.worker import ExecutionResult, run_isolated
from waffle.simulation.common.backend import CancellationToken, RunState
from waffle.simulation.photonics.characterization.publication import (
    PublishedWaveguide,
    publish_waveguide,
)
from waffle.simulation.photonics.characterization.validation import validate_waveguide
from waffle.simulation.photonics.characterization.waveguide import (
    WaveguideEvidence,
    extract_profile_ratio,
)
from waffle.simulation.photonics.device_analysis.slab import (
    SlabLayer,
    SlabStack,
    effective_index,
)


@dataclass(frozen=True)
class CharacterizationResult:
    publication: PublishedWaveguide
    report: ValidationReport
    mapping: ViewMapping | None
    executions: tuple[ExecutionResult, ...]
    backward_fraction: float
    standing_wave_residual: float
    fitted_effective_index: float


def characterize_waveguide(
    problem: PhysicalProblem,
    template: ModelRevision,
    mapping: ViewMapping,
    *,
    command: tuple[str, ...],
    root: Path,
    store: ArtifactStore,
    wavelength_pm: int = 1550000,
    fine_spacing_pm: int = 25000,
    steps: int = 4000,
    time_step_as: int = 50,
    cancellation: CancellationToken | None = None,
) -> CharacterizationResult:
    """Run four independent bounded tests on an analysis-only uniform lead extension.

    Supports one axis-aligned rectangular reduced-index guide, not MMI or arbitrary
    geometry. Source and all five coherent planes are in the same cross-section.
    Qualification checks magnitude/phase, grid/time/PML discrepancies, transverse
    proportionality, and an overdetermined forward/backward longitudinal fit.
    """
    d = problem.description
    if (
        not isinstance(d, Reduced2D)
        or len(d.regions) != 1
        or len(d.regions[0].polygons) != 1
        or len(d.ports) != 2
    ):
        raise ValueError(
            "recipe requires one rectangular reduced guide and two endpoint ports"
        )
    ports = sorted(d.ports, key=lambda port: port.center[0])
    first, last = ports
    polygon = d.regions[0].polygons[0]
    xs, ys = (
        sorted({p[0] for p in polygon.points}),
        sorted({p[1] for p in polygon.points}),
    )
    if (
        len(xs) != 2
        or len(ys) != 2
        or len(polygon.points) != 4
        or first.center[1] != last.center[1]
        or first.center[0] >= last.center[0]
    ):
        raise ValueError(
            "recipe requires straight rectangular geometry and ordered coaxial ports"
        )
    if (
        (first.center[0], last.center[0]) != (xs[0], xs[1])
        or 2 * first.center[1] != ys[0] + ys[1]
        or (first.orientation, last.orientation) != (180, 0)
    ):
        raise ValueError(
            "seed ports must be centered at the original rectangle endpoints with outward normals"
        )
    if problem.dbu_pm != 1000:
        raise ValueError("bounded recipe currently requires 1nm dbu")
    polarization = problem.approximations[0].polarization
    component = "Hz" if polarization is Polarization.TE else "Ez"
    lateral = Polarization.TM if polarization is Polarization.TE else Polarization.TE
    wavelength = wavelength_pm * 1e-12
    frequency = 299792458.0 / wavelength
    nref = effective_index(
        SlabStack(
            d.background_index,
            (SlabLayer((ys[1] - ys[0]) * 1e-9, d.regions[0].index),),
            d.background_index,
        ),
        wavelength,
        lateral,
    )
    higher_mode = effective_index(
        SlabStack(
            d.background_index,
            (SlabLayer((ys[1] - ys[0]) * 1e-9, d.regions[0].index),),
            d.background_index,
        ),
        wavelength,
        lateral,
        1,
    )
    if nref is None:
        raise ValueError("reference guide has no guided lateral mode")
    x0, x1 = first.center[0], last.center[0]
    if (x1 - x0) % 4:
        raise ValueError(
            "endpoint distance must allow four equal integer-dbu intervals"
        )
    center_y = first.center[1]
    planes = tuple(
        PortPlane(
            path=f"p{i}",
            layer=first.layer,
            domain=first.domain,
            center=(x0 + (x1 - x0) * i // 4, center_y),
            width=3000,
            orientation=0,
        )
        for i in range(5)
    )
    domain_lo, domain_hi = (x0 - 10000, center_y - 3000), (x1 + 6000, center_y + 3000)
    region = d.regions[0].model_copy(
        update={
            "polygons": (
                Polygon(
                    points=(
                        (domain_lo[0], ys[0]),
                        (domain_hi[0], ys[0]),
                        (domain_hi[0], ys[1]),
                        (domain_lo[0], ys[1]),
                    )
                ),
            )
        }
    )
    executions: list[ExecutionResult] = []
    evidences: list[WaveguideEvidence] = []
    coordinate_arrays: list[list[float]] = []
    modal_coefficients: list[np.ndarray] = []
    window_ratios: list[complex] = []
    raw_profile_residuals: list[float] = []
    for name, spacing, count, pml in (
        ("fine", fine_spacing_pm, steps, 1000000),
        ("coarse", 2 * fine_spacing_pm, steps, 1000000),
        ("time", fine_spacing_pm, 2 * steps, 1000000),
        ("boundary", fine_spacing_pm, steps, 1500000),
    ):
        derived = problem.model_copy(
            update={
                "id": EntityId("physical_problem", f"characterization_{name}"),
                "description": d.model_copy(
                    update={
                        "domain": Domain2D(
                            lo=domain_lo,
                            hi=domain_hi,
                            boundaries=Boundaries2D(
                                **{
                                    face: BoundaryCondition(thickness_pm=pml)
                                    for face in ("x_min", "x_max", "y_min", "y_max")
                                }
                            ),
                        ),
                        "regions": (region,),
                        "ports": planes,
                    }
                ),
                "metadata": {
                    **problem.metadata,
                    "analysis_extension": "uniform guide continued through x PML; not a new fabricated layout",
                    "seed_problem": str(revision_of(problem)),
                },
            }
        )
        request = AnalysisRequest(
            id=f"analysis_request:characterization_{name}",
            problem=revision_of(derived),
            polarization=polarization,
            excitations=(
                Excitation(
                    kind="point",
                    center_dbu=(x0 - 8000, center_y),
                    wavelength_pm=wavelength_pm,
                ),
            ),
            observables=tuple(
                Observable(
                    name=plane.path,
                    kind="raw_field",
                    field_component=component,
                    port=plane.path,
                )
                for plane in planes
            ),
            numerical=NumericalSettings(
                grid_spacing_pm=spacing, time_step_as=time_step_as, steps=count
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
        executions.append(execution)
        if execution.run.state is not RunState.COMPLETE:
            raise ValueError(
                f"{name} worker failed: {execution.run.diagnostics}; {execution.directory}"
            )
        monitors = execution.effective["monitors"]
        if execution.effective.get("time_convention") != "exp(-i omega t)":
            raise ValueError("worker did not verify coherent time convention")
        profiles = {a.name: np.asarray(a.payload) for a in execution.run.acquisitions}
        coords = [float(monitors[p.path]["plane_coordinate_m"]) for p in planes]
        coordinate_arrays.append(coords)
        # Every plane must sample the same physical transverse coordinates.
        samples = [np.asarray(monitors[p.path]["sample_coordinates_m"]) for p in planes]
        if any(
            not np.allclose(item[:, 1], samples[0][:, 1], atol=1e-15, rtol=0)
            for item in samples[1:]
        ):
            raise ValueError("monitor transverse coordinates differ")
        evidence = extract_profile_ratio(
            (frequency,),
            profiles["p0"],
            profiles["p4"],
            upstream_plane_m=coords[0],
            downstream_plane_m=coords[-1],
            field_component=component,
            output_channel=last.path,
            excitation_channel=first.path,
            grid_spacing_m=spacing * 1e-12,
            time_step_s=time_step_as * 1e-18,
            steps=count,
            maximum_profile_residual=1.0,
            raw_artifacts=(str(execution.artifact),),
        ).unwrap()
        raw_profile_residuals.append(evidence.samples[0].profile_residual)
        y = samples[0][:, 1] - center_y * 1e-9
        half_width = (ys[1] - ys[0]) * 1e-9 / 2
        k = 2 * np.pi / wavelength * np.sqrt(d.regions[0].index ** 2 - nref**2)
        gamma = 2 * np.pi / wavelength * np.sqrt(nref**2 - d.background_index**2)
        mode = np.where(
            abs(y) <= half_width,
            np.cos(k * y),
            np.cos(k * half_width) * np.exp(-gamma * (abs(y) - half_width)),
        )
        weight = (
            np.ones_like(y)
            if lateral is Polarization.TE
            else 1
            / np.where(
                abs(y) <= half_width, d.regions[0].index ** 2, d.background_index**2
            )
        )
        coefficients = np.asarray(
            [
                np.sum(mode * weight * profiles[p.path][0])
                / np.sum(mode * weight * mode)
                for p in planes
            ]
        )
        inner = abs(y) <= 1e-6
        inner_coefficients = np.asarray(
            [
                np.sum(mode[inner] * weight[inner] * profiles[p.path][0, inner])
                / np.sum(mode[inner] * weight[inner] * mode[inner])
                for p in planes
            ]
        )
        if not np.all(np.isfinite(coefficients)) or np.any(
            abs(coefficients) <= 1e-12 * max(float(np.max(abs(coefficients))), 1e-30)
        ):
            raise ValueError(
                "modal projection is zero, nonfinite, or numerically unresolvable"
            )
        ratio = complex(coefficients[-1] / coefficients[0])
        modal_coefficients.append(coefficients)
        window_ratios.append(complex(inner_coefficients[-1] / inner_coefficients[0]))
        evidence = evidence.model_copy(
            update={
                "extraction": "modal_overlap_ratio_v1",
                "normalization": "same_mode_overlap_ratio",
                "samples": (
                    evidence.samples[0].model_copy(
                        update={"real": ratio.real, "imag": ratio.imag}
                    ),
                ),
            }
        )
        evidences.append(evidence)
    fine, coarse, time, boundary = evidences
    length = fine.downstream_plane_m - fine.upstream_plane_m
    if not np.isclose(length, (x1 - x0) * 1e-9, rtol=0, atol=1e-15):
        raise ValueError(
            "actual sampled monitor spacing differs from pinned layout endpoint length"
        )
    reference = cmath.exp(2j * cmath.pi * frequency * nref * length / 299792458)
    report = validate_waveguide(
        fine,
        coarse,
        {frequency: reference},
        report_id="validation_report:waveguide",
        reference_description=f"Symmetric lossless lateral slab {lateral.value}, n_eff={nref:.17g}; scalar analytical reference v1; original endpoint planes",
        channels=(first.path, last.path),
    )
    comparisons = list(report.comparisons)
    comparisons.append(
        Comparison(
            quantity="additional_lateral_guided_mode",
            error=1 if higher_mode is not None else 0,
            tolerance=0,
            unit="1",
            frequency_hz=frequency,
            comparison="analytic_reference",
        )
    )
    for kind, other in (("time_convergence", time), ("boundary_convergence", boundary)):
        if (fine.upstream_plane_m, fine.downstream_plane_m) != (
            other.upstream_plane_m,
            other.downstream_plane_m,
        ):
            raise ValueError("convergence runs sampled different reference planes")
        comparisons.extend(
            compare_complex(
                fine.samples[0].amplitude,
                other.samples[0].amplitude,
                frequency,
                magnitude_tolerance=0.05,
                phase_tolerance_rad=0.15,
                comparison=kind,
            )
        )
    longitudinal = modal_coefficients[0] / modal_coefficients[0][0]
    positions = np.asarray(coordinate_arrays[0]) - coordinate_arrays[0][0]
    best = None
    for index in np.linspace(nref * 0.85, nref * 1.15, 1001):
        beta = 2 * np.pi * frequency * index / 299792458
        matrix = np.stack(
            (np.exp(1j * beta * positions), np.exp(-1j * beta * positions)), axis=1
        )
        coefficients = np.linalg.lstsq(matrix, longitudinal, rcond=None)[0]
        residual = float(
            np.linalg.norm(matrix @ coefficients - longitudinal)
            / np.linalg.norm(longitudinal)
        )
        candidate = (
            residual,
            float(index),
            float(abs(coefficients[1]) / max(abs(coefficients[0]), 1e-30)),
        )
        if best is None or candidate[0] < best[0]:
            best = candidate
    assert best is not None
    residual, index, backward = best
    for quantity, error, tolerance in (
        ("backward_amplitude_fraction", backward, 0.05),
        ("standing_wave_fit_residual", residual, 0.05),
        (
            "modal_window_amplitude_difference",
            abs(abs(fine.samples[0].amplitude) - abs(window_ratios[0])),
            0.01,
        ),
        (
            "modal_window_phase_difference",
            abs(cmath.phase(fine.samples[0].amplitude / window_ratios[0])),
            0.02,
        ),
    ):
        comparisons.append(
            Comparison(
                quantity=quantity,
                error=error,
                tolerance=tolerance,
                unit="1",
                frequency_hz=frequency,
                comparison="analytic_reference",
            )
        )
    inputs = (
        InputLink(role="seed_problem", target=revision_of(problem)),
        *(
            InputLink(role="simulation", target=execution.artifact)
            for execution in executions
            if execution.artifact is not None
        ),
    )
    report = report.model_copy(
        update={
            "comparisons": tuple(comparisons),
            "metadata": {
                "extraction": "modal_overlap_ratio_v1",
                "modal_reference_index": str(nref),
                "fitted_effective_index": str(index),
                "raw_profile_residual": str(raw_profile_residuals[0]),
                "integration_window_m": "3e-6",
                "comparison_window_m": "2e-6",
                "source_distance_m": "8e-6",
                "common_plane_translation_m": str(fine.upstream_plane_m - x0 * 1e-9),
            },
            "inputs": tuple(link.target for link in inputs),
            "limitations": (
                "Analysis-only uniform leads extend original rectangle through x PML; endpoint planes remain original design ports.",
                f"Analytic fundamental-mode projection v1: scalar lateral {lateral.value}; 3um transverse window checked against 2um; raw profile residual={raw_profile_residuals[0]:.17g}, radiation remains unmodeled.",
                "Forward/back fit optimizes beta over +/-15% with 1001 samples; this is a bounded diagnostic, not a general mode decomposition.",
                "Only exact carrier sample and forward transfer tested; reverse/reflection matrix elements are uncomputed.",
                "Two-grid/time/PML discrepancies are tested tolerances, not statistical uncertainty bounds.",
            ),
        }
    )
    qualified = report.passed
    publication = publish_waveguide(
        store,
        template,
        fine,
        report,
        inputs=inputs,
        modal_qualification=(
            "Uniform single lateral mode; all magnitude/phase, independent grid/time/PML, analytic mode overlap window and longitudinal backward fraction checks passed stated report tolerances."
            if qualified
            else None
        ),
    )
    published_mapping = (
        mapping.model_copy(
            update={
                "model": revision_of(publication.model),
                "responses": tuple(
                    ResponseChannel(
                        output=out,
                        excitation=exc,
                        status=ResponseStatus.COMPUTED
                        if (out, exc) == (last.path, first.path)
                        else ResponseStatus.UNCOMPUTED,
                    )
                    for out in (first.path, last.path)
                    for exc in (first.path, last.path)
                ),
            }
        )
        if publication.model is not None
        else None
    )
    return CharacterizationResult(
        publication,
        report,
        published_mapping,
        tuple(executions),
        backward,
        residual,
        index,
    )
