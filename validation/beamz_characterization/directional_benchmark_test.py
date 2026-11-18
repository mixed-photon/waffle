"""Real bidirectional benchmark of the uniform 2 µm reduced TM guide."""

from __future__ import annotations

import cmath
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from waffle_pdk_demo import waveguide

from waffle.artifacts.local import LocalArtifactStore
from waffle.characterization.validation.compare import compare_complex
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
)
from waffle.contracts.characterization.report import Comparison, ValidationReport
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import (
    Boundaries2D,
    BoundaryCondition,
    Domain2D,
    PhysicalProblem,
    PortPlane,
    Reduced2D,
)
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.execution.worker import ExecutionResult, run_isolated
from waffle.simulation.common.backend import RunState
from waffle.simulation.photonics.characterization.directional import (
    DirectionalFit,
    estimate_two_port,
    fit_directional_waves,
)

WAVELENGTH_PM = 1_550_000
WAVELENGTH_M = WAVELENGTH_PM * 1e-12
FREQUENCY_HZ = 299_792_458.0 / WAVELENGTH_M
K0 = 2 * np.pi / WAVELENGTH_M
REFERENCES_DBU = (0, 2000)
LEFT_OFFSETS_DBU = (-1500, -1250, -975, -650, -400, -175, 75, 300)
TIME_STEP_AS = 25


@dataclass(frozen=True)
class Settings:
    name: str
    spacing_pm: int
    steps: int
    pml_pm: int


SETTINGS = (
    Settings("fine", 12_500, 12_000, 1_500_000),
    Settings("medium", 20_000, 12_000, 1_500_000),
    Settings("coarse", 25_000, 12_000, 1_500_000),
    Settings("duration", 12_500, 8_000, 1_500_000),
    Settings("boundary", 12_500, 12_000, 1_000_000),
)


def _problem(seed: PhysicalProblem, settings: Settings) -> PhysicalProblem:
    description = seed.description
    assert isinstance(description, Reduced2D)
    assert seed.dbu_pm == 1000
    assert tuple(port.center for port in description.ports) == ((0, 0), (2000, 0))
    (region,) = description.regions
    (waveguide_polygon,) = region.polygons
    assert {y for _, y in waveguide_polygon.points} == {-250, 250}
    lo, hi = (-10_000, -3000), (12_000, 3000)
    extension = Polygon(
        points=((lo[0], -250), (hi[0], -250), (hi[0], 250), (lo[0], 250))
    )
    boundary = BoundaryCondition(thickness_pm=settings.pml_pm)
    ports = tuple(
        PortPlane(
            path=f"{side}{index}",
            layer=region.layer,
            domain=description.ports[0].domain,
            center=(offset + plane, 0),
            width=3000,
            orientation=0,
        )
        for side, plane, offsets in (
            ("left", REFERENCES_DBU[0], LEFT_OFFSETS_DBU),
            ("right", REFERENCES_DBU[1], tuple(-x for x in reversed(LEFT_OFFSETS_DBU))),
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


def _launch(
    seed: PhysicalProblem,
    settings: Settings,
    source_x: int,
    worker: Path,
    store: LocalArtifactStore,
    root: Path,
) -> ExecutionResult:
    problem = _problem(seed, settings)
    request = AnalysisRequest(
        id=EntityId(
            "analysis_request",
            f"directional_{settings.name}_{'left' if source_x < 0 else 'right'}",
        ),
        problem=revision_of(problem),
        polarization=Polarization.TM,
        excitations=(
            Excitation(
                kind="point", center_dbu=(source_x, 0), wavelength_pm=WAVELENGTH_PM
            ),
        ),
        observables=tuple(
            Observable(name=p.path, kind="raw_field", field_component="Ez", port=p.path)
            for p in problem.description.ports
        ),
        numerical=NumericalSettings(
            grid_spacing_pm=settings.spacing_pm,
            time_step_as=TIME_STEP_AS,
            steps=settings.steps,
        ),
        backend_settings=BackendSettings(
            backend="beamz",
            version="0.6.2",
            schema_version=1,
            parameters={
                "source_width_pm": 100_000,
                "pulse_center_as": 40_000,
                "pulse_width_as": 12_000,
            },
        ),
    )
    run = run_isolated(problem, request, command=(str(worker),), root=root, store=store)
    assert run.run.state is RunState.COMPLETE, (run.run.diagnostics, run.directory)
    assert run.artifact is not None
    return run


def _fit(
    run: ExecutionResult,
    core: float,
    clad: float,
    nref: float,
    half_window_m: float,
) -> DirectionalFit:
    assert run.effective["time_convention"] == "exp(-i omega t)"
    k = K0 * np.sqrt(core**2 - nref**2)
    gamma = K0 * np.sqrt(nref**2 - clad**2)
    half_width_m = 0.25e-6
    acquisitions = {item.name: item for item in run.run.acquisitions}
    offsets, fields = [], []
    for side, plane in zip(("left", "right"), REFERENCES_DBU, strict=True):
        positions, projections = [], []
        for index in range(len(LEFT_OFFSETS_DBU)):
            name = f"{side}{index}"
            monitor = run.effective["monitors"][name]
            assert monitor["normal_axis"] == "x" and monitor["field_component"] == "Ez"
            assert np.allclose(
                monitor["frequencies_hz"], [FREQUENCY_HZ], rtol=1e-12, atol=0
            )
            coords = np.asarray(monitor["sample_coordinates_m"])
            x = monitor["plane_coordinate_m"]
            assert np.allclose(coords[:, 0], x, rtol=0, atol=1e-15)
            assert np.allclose(
                np.diff(coords[:, 1]), run.effective["dx_m"], rtol=0, atol=1e-15
            )
            acquisition = acquisitions[name]
            assert acquisition.axes == ("frequency", "transverse")
            assert acquisition.shape == (1, len(coords))
            cut = abs(coords[:, 1]) <= half_window_m
            y = abs(coords[cut, 1])
            mode = np.where(
                y <= half_width_m,
                np.cos(k * y),
                np.cos(k * half_width_m) * np.exp(-gamma * (y - half_width_m)),
            )
            values = np.asarray(acquisition.payload)[0, cut]
            positions.append(x - plane * 1e-9)
            projections.append(np.dot(mode, values) / np.dot(mode, mode))
        offsets.append(positions)
        fields.append(projections)
    return fit_directional_waves(
        np.asarray(offsets), np.asarray(fields), K0 * nref
    ).unwrap()


def test_directional_benchmark() -> None:
    root = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    worker = Path("third_party/beamz/worker").resolve()
    store = LocalArtifactStore(root / "store")
    device = waveguide(length_um=2, polarization=Polarization.TM)
    reduced = device.problem.description
    assert isinstance(reduced, Reduced2D)
    core, clad, nref = (
        reduced.regions[0].index,
        reduced.background_index,
        device.effective_index,
    )
    runs = {
        settings.name: tuple(
            _launch(device.problem, settings, source_x, worker, store, root / "workers")
            for source_x in (-8000, 10000)
        )
        for settings in SETTINGS
    }
    fits = {
        name: tuple(_fit(run, core, clad, nref, window) for run in pair)
        for name, pair, window in (
            *((name, pair, 1.5e-6) for name, pair in runs.items()),
            ("window", runs["fine"], 1.0e-6),
        )
    }
    estimates = {name: estimate_two_port(*pair).unwrap() for name, pair in fits.items()}
    fine = estimates["fine"].scattering
    reference = cmath.exp(1j * K0 * nref * 2e-6)
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
    links = (
        InputLink(role="seed_problem", target=revision_of(device.problem)),
        *(
            InputLink(role="simulation", target=run.artifact)
            for pair in runs.values()
            for run in pair
            if run.artifact is not None
        ),
    )
    report = ValidationReport(
        id="validation_report:directional_waveguide",
        procedure="uniform_guide_directional_two_port",
        procedure_version="1",
        inputs=tuple(link.target for link in links),
        reference_description="Lossless symmetric single-mode lateral slab, equal power normalization at both ports, zero reflection",
        comparisons=tuple(comparisons),
        observed_channels=(("o1", "o1"), ("o1", "o2"), ("o2", "o1"), ("o2", "o2")),
        limitations=(
            "Only the 2D reduced-index 1550 nm TM benchmark at the pinned endpoint planes.",
            "Boundary sensitivity and residual modal/radiation contamination remain numerical error.",
            "Reflection phase is not compared to a zero analytic amplitude. No interpolation or experimental validation.",
            "These estimates qualify the benchmark only. No replacement release model is published.",
        ),
    )
    summary = {
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
                "left_launch_total_field_ratio_magnitude": abs(
                    (fits[name][0].right.positive_x + fits[name][0].right.negative_x)
                    / (fits[name][0].left.positive_x + fits[name][0].left.negative_x)
                ),
            }
            for name, estimate in estimates.items()
        },
        "report": report.model_dump(mode="json"),
    }
    operation = OperationInfo(
        name="waveguide.directional_benchmark",
        version="1",
        parameters_digest=content_hash(summary["settings"]),
    )
    with store.attempt(operation, links) as attempt:
        writer = attempt.new_artifact("waveguide_benchmark")
        writer.write_json("report.json", summary)
        artifact = writer.commit()
    (root / "directional.json").write_text(
        json.dumps({**summary, "artifact": str(artifact)}, indent=2) + "\n",
        encoding="utf-8",
    )
    assert report.passed, [item for item in comparisons if not item.passed]
