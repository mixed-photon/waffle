"""Bounded CPU BeamZ 0.6.2 adapter; all imports of the solver stay here."""

from __future__ import annotations

import importlib.metadata
import math
import platform
from typing import Any

import numpy as np

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    ObservableKind,
)
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import BoundaryKind, PhysicalProblem, Reduced2D
from waffle.geometry.services.shapes import is_simple
from waffle.simulation.common.backend import (
    Acquisition,
    BackendCapabilities,
    BackendRun,
    CancellationToken,
    ProgressCallback,
    ProgressEvent,
    RunState,
)


def _error(message: str) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code="beamz.unsupported", message=message
    )


def rasterize(problem: PhysicalProblem, spacing_pm: int) -> np.ndarray:
    """Cell-centre, binary polygon raster, in painter order, (y,x), epsilon_r=n²."""
    d = problem.description
    assert isinstance(d, Reduced2D)
    nx, ny = (
        (d.domain.hi[i] - d.domain.lo[i]) * problem.dbu_pm // spacing_pm for i in (0, 1)
    )
    x, y = np.meshgrid(
        d.domain.lo[0] + (np.arange(nx) + 0.5) * spacing_pm / problem.dbu_pm,
        d.domain.lo[1] + (np.arange(ny) + 0.5) * spacing_pm / problem.dbu_pm,
    )
    epsilon = np.full((ny, nx), d.background_index**2)
    for region in d.regions:
        for polygon in region.polygons:
            inside = np.zeros((ny, nx), dtype=bool)
            points = polygon.points
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
                if ay != by:
                    inside ^= ((ay > y) != (by > y)) & (
                        x < (bx - ax) * (y - ay) / (by - ay) + ax
                    )
            epsilon[inside] = region.index**2
    return epsilon


class BeamZBackend:
    """Raw point-excited fields only; no inferred modal or normalized observables."""

    def __init__(self) -> None:
        self.last_metadata: dict[str, Any] = {}

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            "beamz",
            "0.6.2",
            frozenset({"2d"}),
            frozenset(Polarization),
            frozenset({"point"}),
            frozenset({0}),
            frozenset({ObservableKind.RAW_FIELD}),
            frozenset({BoundaryKind.ABSORBING}),
            frozenset({"Ez", "Hz"}),
            frozenset({1}),
            True,
            True,
        )

    def validate_settings(self, settings: BackendSettings) -> tuple[Diagnostic, ...]:
        p = settings.parameters
        errors: list[Diagnostic] = []
        if set(p) - {
            "source_width_pm",
            "pulse_center_as",
            "pulse_width_as",
            "record_interval",
        }:
            errors.append(_error("unknown settings keys"))
        for key, value in p.items():
            if type(value) is not int or value <= 0:
                errors.append(_error(f"{key} must be a positive integer"))
        return tuple(errors)

    def validate_problem(
        self, problem: PhysicalProblem, request: AnalysisRequest
    ) -> tuple[Diagnostic, ...]:
        errors: list[Diagnostic] = []
        d = problem.description
        if not isinstance(d, Reduced2D):
            return (_error("only lossless scalar reduced-index 2D is supported"),)
        if self.validate_settings(request.backend_settings):
            return ()
        n = request.numerical
        if n.steps < 2:
            errors.append(_error("time array needs at least two samples"))
        if len(request.excitations) != 1:
            errors.append(_error("exactly one point excitation is supported"))
        if any(
            not is_simple(polygon)
            for region in d.regions
            for polygon in region.polygons
        ):
            errors.append(_error("self-intersecting polygons are unsupported"))
        lengths = [(d.domain.hi[i] - d.domain.lo[i]) * problem.dbu_pm for i in (0, 1)]
        if any(length % n.grid_spacing_pm for length in lengths):
            errors.append(_error("domain extents must be exact multiples of spacing"))
        p = request.backend_settings.parameters
        interval = p.get("record_interval", 10)
        if interval > n.steps:
            errors.append(_error("record interval cannot exceed the step count"))
        source_width = p.get("source_width_pm", 2 * n.grid_spacing_pm)
        if source_width < n.grid_spacing_pm or source_width > min(lengths) / 4:
            errors.append(
                _error(
                    "Gaussian source width must be >=one cell and <=quarter the smaller extent"
                )
            )
        counts = [length // n.grid_spacing_pm for length in lengths]
        if min(counts) < 8 or math.prod(counts) > 1_000_000:
            errors.append(
                _error(
                    "grid must have 8 or more cells per axis and at most 1000000 cells"
                )
            )
        indices = [d.background_index, *(r.index for r in d.regions)]
        if min(indices) < 1 or max(indices) > 10:
            errors.append(_error("indices must be between 1 and 10"))
        if (
            n.time_step_as * 1e-18
            > n.grid_spacing_pm
            * 1e-12
            * min(indices)
            / (299792458 * math.sqrt(2))
            * 0.99
        ):
            errors.append(_error("time step violates conservative 2D CFL limit"))
        boundaries = [v for _, v in d.domain.boundaries]
        thicknesses = {v.thickness_pm for v in boundaries}
        if (
            len(thicknesses) != 1
            or None in thicknesses
            or any(v.kind is not BoundaryKind.ABSORBING for v in boundaries)
        ):
            errors.append(
                _error("all four faces require explicit equal absorbing thickness")
            )
        else:
            thickness = next(iter(thicknesses))
            assert thickness is not None
            if (
                thickness % n.grid_spacing_pm
                or thickness < 2 * n.grid_spacing_pm
                or 2 * thickness >= min(lengths)
            ):
                errors.append(
                    _error(
                        "PML needs >=2 cells, grid-aligned and less than half each extent"
                    )
                )
            for excitation in request.excitations:
                if excitation.center_dbu is not None and any(
                    not thickness
                    < (excitation.center_dbu[i] - d.domain.lo[i]) * problem.dbu_pm
                    < lengths[i] - thickness
                    for i in (0, 1)
                ):
                    errors.append(_error("source must lie strictly outside PML"))
        component = "Ez" if request.polarization is Polarization.TM else "Hz"
        ports = {p.path: p for p in d.ports}
        for observable in request.observables:
            if observable.field_component != component:
                errors.append(_error(f"polarization requires scalar {component}"))
            if observable.port is not None and observable.port in ports:
                p = ports[observable.port]
                normal = 0 if p.orientation in (0, 180) else 1
                for axis in (0, 1):
                    half = p.width / 2 if axis != normal else 0
                    if (
                        p.center[axis] - half < d.domain.lo[axis]
                        or p.center[axis] + half > d.domain.hi[axis]
                    ):
                        errors.append(_error("port monitor must fit inside domain"))
        estimated = math.prod(counts) * 128 + sum(
            math.prod(counts)
            * 8
            * (
                (n.steps - 1)
                // request.backend_settings.parameters.get("record_interval", 10)
                + 1
            )
            for o in request.observables
            if o.port is None
        )
        if estimated > request.resources.memory_bytes:
            errors.append(
                _error("requested field recording exceeds memory budget estimate")
            )
        return tuple(errors)

    def execute(
        self,
        problem: PhysicalProblem,
        request: AnalysisRequest,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> BackendRun:
        from waffle.simulation.photonics.device_analysis.analysis import preflight

        self.last_metadata = {
            "schema_version": 1,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "requested_backend": request.backend_settings.model_dump(mode="json"),
            "termination": "preflight",
            "numerical": request.numerical.model_dump(mode="json"),
        }
        for package in ("beamz", "jax", "numpy"):
            try:
                self.last_metadata[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                self.last_metadata[package] = "unavailable"
        errors = preflight(problem, request, None, self.capabilities(), self)
        if errors:
            self.last_metadata["termination"] = "rejected"
            return BackendRun(RunState.REJECTED, (), 0, errors)
        if cancellation is not None and cancellation.cancelled():
            self.last_metadata["termination"] = "cancelled"
            return BackendRun(RunState.CANCELLED, (), 0)
        import beamz as bz
        import jax
        from beamz.design.discretization import MaterialGrid

        if importlib.metadata.version("beamz") != "0.6.2":
            return BackendRun(
                RunState.REJECTED,
                (),
                0,
                (_error("installed BeamZ version differs from 0.6.2"),),
            )
        if any(device.platform != "cpu" for device in jax.devices()):
            return BackendRun(
                RunState.REJECTED, (), 0, (_error("this adapter requires JAX CPU"),)
            )
        d = problem.description
        assert isinstance(d, Reduced2D)
        n = request.numerical
        dx, dt = n.grid_spacing_pm * 1e-12, n.time_step_as * 1e-18
        epsilon = rasterize(problem, n.grid_spacing_pm)
        sizes = tuple(problem.metres(d.domain.hi[i] - d.domain.lo[i]) for i in (0, 1))
        midpoint = tuple(
            problem.metres(d.domain.hi[i] + d.domain.lo[i]) / 2 for i in (0, 1)
        )
        grid = MaterialGrid(
            epsilon,
            0.0,
            1.0,
            dx,
            epsilon.shape,
            origin=(-sizes[0] / 2, -sizes[1] / 2, 0.0),
            polarization=request.polarization.value,
        )
        p = request.backend_settings.parameters
        time = np.arange(n.steps) * dt
        frequency = 299792458 / (request.excitations[0].wavelength_pm * 1e-12)
        center = p.get("pulse_center_as", int(6e18 / frequency)) * 1e-18
        width = p.get("pulse_width_as", int(2e18 / frequency)) * 1e-18
        signal = np.exp(-(((time - center) / width) ** 2)) * np.cos(
            2 * np.pi * frequency * (time - center)
        )
        sources = [
            bz.GaussianSource(
                position=tuple(
                    problem.metres(e.center_dbu[i]) - midpoint[i] for i in (0, 1)
                ),
                width=p.get("source_width_pm", 2 * n.grid_spacing_pm) * 1e-12,
                signal=signal,
            )
            for e in request.excitations
        ]
        monitors = []
        for o in request.observables:
            if o.port is None:
                monitors.append(
                    bz.FieldRecorder(
                        (o.field_component,),
                        interval=p.get("record_interval", 10),
                        name=o.name,
                    )
                )
            else:
                port = next(port for port in d.ports if port.path == o.port)
                size = (
                    (0.0, problem.metres(port.width), 0.0)
                    if port.orientation in (0, 180)
                    else (problem.metres(port.width), 0.0, 0.0)
                )
                monitors.append(
                    bz.FieldMonitor(
                        center=(
                            *[
                                problem.metres(port.center[i]) - midpoint[i]
                                for i in (0, 1)
                            ],
                            0.0,
                        ),
                        size=size,
                        freqs=np.array([frequency]),
                        fields=(o.field_component,),
                        name=o.name,
                    )
                )
        simulation = bz.Simulation(
            material_grid=grid,
            time=time,
            polarization=request.polarization.value,
            sources=sources,
            monitors=monitors,
            boundaries=[
                bz.PML(thickness=d.domain.boundaries.x_min.thickness_pm * 1e-12)
            ],
            normalize_source=None,
        )
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "input_fingerprint": content_hash(
                {
                    "problem": problem.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                }
            ),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source_width_m": p.get("source_width_pm", 2 * n.grid_spacing_pm) * 1e-12,
            "pulse_center_s": center,
            "pulse_width_s": width,
            "record_interval": p.get("record_interval", 10),
            "beamz": importlib.metadata.version("beamz"),
            "jax": jax.__version__,
            "devices": [str(v) for v in jax.devices()],
            "dx_m": dx,
            "dt_s": dt,
            "steps": n.steps,
            "epsilon_shape_yx": list(epsilon.shape),
            "raster": "cell_center_binary_painter_order",
            "origin_m": [problem.metres(v) for v in d.domain.lo],
            "public_midpoint_m": midpoint,
            "frequency_hz": frequency,
            "normalize_source": None,
            "time_convention": "exp(-i omega t)",
            "dft_kernel": "exp(+i omega t)",
            "monitors": {},
        }
        self.last_metadata = metadata
        if progress:
            progress(ProgressEvent(0, n.steps, "running"))
        try:
            result = simulation.run(backend="jax", performance=False)
            acquisitions = []
            for o in request.observables:
                m = result[o.name]
                values = np.asarray(
                    m.fields[o.field_component]
                    if o.port is None
                    else m.get_dft_component(o.field_component)
                )
                axes = (
                    ("time", "y", "x")
                    if o.port is None
                    else ("frequency", "transverse")
                )
                acquisitions.append(
                    Acquisition(
                        o.name,
                        ObservableKind.RAW_FIELD,
                        values,
                        "V/m" if o.field_component.startswith("E") else "A/m",
                        axes,
                        values.shape,
                    )
                )
                info: dict[str, Any] = {
                    "field_component": o.field_component,
                    "port": o.port,
                }
                if o.port is None:
                    info.update(
                        times_s=np.asarray(m.field_times).tolist(),
                        steps=np.asarray(m.field_steps).tolist(),
                        x_coordinates_m=(
                            problem.metres(d.domain.lo[0])
                            + (
                                np.arange(values.shape[-1])
                                + (0.0 if o.field_component == "Ez" else 0.5)
                            )
                            * dx
                        ).tolist(),
                        y_coordinates_m=(
                            problem.metres(d.domain.lo[1])
                            + (
                                np.arange(values.shape[-2])
                                + (0.0 if o.field_component == "Ez" else 0.5)
                            )
                            * dx
                        ).tolist(),
                    )
                else:
                    port = next(port for port in d.ports if port.path == o.port)
                    translated = next(
                        monitor
                        for monitor in simulation.monitors
                        if monitor.name == o.name
                    )
                    points = translated.get_grid_points_2d(dx, dx)
                    offset = 0.0 if o.field_component == "Ez" else 0.5
                    coordinates = [
                        [
                            (point[i] + offset) * dx + problem.metres(d.domain.lo[i])
                            for i in (0, 1)
                        ]
                        for point in points
                    ]
                    if len(coordinates) != values.shape[-1]:
                        raise ValueError(
                            "public monitor sampling coordinates disagree with acquired shape"
                        )
                    normal = 0 if port.orientation in (0, 180) else 1
                    info.update(
                        frequencies_hz=np.asarray(m.dft_frequencies).tolist(),
                        dft_weight_sum=np.asarray(m.dft_weight_sum).tolist(),
                        dft_amplitude_scale=float(m.dft_amplitude_scale),
                        dft_scaling="public_get_dft_component_temporal_average_no_source_normalization",
                        normal_axis="xy"[normal],
                        plane_coordinate_m=coordinates[0][normal],
                        sample_coordinates_m=coordinates,
                        orientation=port.orientation,
                        sampling="public_get_grid_points_2d_direct_Yee_support",
                    )
                metadata["monitors"][o.name] = info
            self.last_metadata = {**metadata, "termination": "complete"}
            if cancellation is not None and cancellation.cancelled():
                self.last_metadata["termination"] = "cancelled"
                return BackendRun(RunState.CANCELLED, tuple(acquisitions), n.steps)
            if progress:
                progress(ProgressEvent(n.steps, n.steps, "complete"))
            return BackendRun(RunState.COMPLETE, tuple(acquisitions), n.steps)
        except (ValueError, RuntimeError, FloatingPointError) as exc:
            self.last_metadata["termination"] = "failed"
            return BackendRun(
                RunState.FAILED,
                (),
                0,
                (_error(f"solver failed: {type(exc).__name__}: {exc}"),),
            )
