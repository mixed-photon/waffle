"""Preflight, execution, and lossless extraction for device analysis."""

from __future__ import annotations

from dataclasses import dataclass

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    Observable,
    ObservableKind,
)
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.mappings.mapping import ModalTransform, Normalization, ViewMapping
from waffle.contracts.physics.approximations import EffectiveIndexReduction
from waffle.contracts.physics.problem import PhysicalProblem, Reduced2D
from waffle.simulation.common.backend import (
    Acquisition,
    BackendCapabilities,
    BackendRun,
    CancellationToken,
    ElectromagneticBackend,
    ProgressCallback,
    RunState,
)


@dataclass(frozen=True)
class AnalysisOutcome:
    """The acquired subset stays inspectable even on cancellation or failure."""

    state: RunState
    acquired: tuple[Acquisition, ...]
    missing: tuple[str, ...]
    completed_steps: int
    backend: str
    backend_version: str
    request: AnalysisRequest
    diagnostics: tuple[Diagnostic, ...]

    @property
    def complete(self) -> bool:
        return self.state is RunState.COMPLETE and not self.missing


def _diagnostic(code: str, message: str, request: AnalysisRequest) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=str(request.id)
    )


def preflight(
    problem: PhysicalProblem,
    request: AnalysisRequest,
    mapping: ViewMapping | None,
    capabilities: BackendCapabilities,
    backend: ElectromagneticBackend,
    *,
    progress: bool = False,
    cancellation: bool = False,
) -> tuple[Diagnostic, ...]:
    """Collect caller-actionable incompatibilities before launching a solver."""
    errors: list[Diagnostic] = []

    def reject(code: str, message: str) -> None:
        errors.append(_diagnostic(code, message, request))

    if request.problem != revision_of(problem):
        reject(
            "analysis.problem.stale",
            "request does not pin this physical problem revision",
        )
    if request.mapping is not None:
        if mapping is None or request.mapping != revision_of(mapping):
            reject(
                "analysis.mapping.stale",
                "request does not pin the supplied mapping revision",
            )
    elif mapping is not None:
        reject(
            "analysis.mapping.unrequested",
            "a mapping was supplied but not pinned by the request",
        )
    if mapping is not None and (
        mapping.problem != request.problem or mapping.layout != problem.layout
    ):
        reject(
            "analysis.mapping.mismatch", "mapping and problem source revisions differ"
        )

    settings = request.backend_settings
    if (
        settings.backend != capabilities.backend
        or settings.version != capabilities.version
    ):
        reject(
            "analysis.backend.version",
            "requested backend identity/version differs from the selected backend",
        )
    if settings.schema_version not in capabilities.settings_versions:
        reject(
            "analysis.backend.settings_version",
            "backend settings schema version is unsupported",
        )
    errors.extend(backend.validate_settings(settings))
    errors.extend(backend.validate_problem(problem, request))
    if progress and not capabilities.supports_progress:
        reject(
            "analysis.progress.unsupported",
            "backend does not support progress reporting",
        )
    if cancellation and not capabilities.supports_cancellation:
        reject(
            "analysis.cancellation.unsupported", "backend does not support cancellation"
        )

    description = problem.description
    dimension = description.dimensionality
    if dimension not in capabilities.dimensionalities:
        reject(
            "analysis.dimension.unsupported",
            f"backend cannot execute {dimension} problems",
        )
    if isinstance(description, Reduced2D):
        reductions = [
            a for a in problem.approximations if isinstance(a, EffectiveIndexReduction)
        ]
        if len(reductions) != 1 or request.polarization != reductions[0].polarization:
            reject(
                "analysis.polarization.mismatch",
                "2D polarization must match the effective-index reduction",
            )
        elif request.polarization not in capabilities.polarizations:
            reject(
                "analysis.polarization.unsupported",
                "backend does not support the requested 2D polarization",
            )
    elif request.polarization is not None:
        reject(
            "analysis.polarization.unexpected",
            "3D analysis does not select a 2D polarization",
        )
    for _, condition in description.domain.boundaries:
        if condition.kind not in capabilities.boundaries:
            reject(
                "analysis.boundary.unsupported",
                f"backend cannot map {condition.kind} boundaries",
            )
    ports = {port.path for port in description.ports}
    for excitation in request.excitations:
        if excitation.kind not in capabilities.excitation_kinds:
            reject(
                "analysis.excitation.unsupported",
                f"backend cannot launch {excitation.kind}",
            )
        if excitation.port is not None and excitation.port not in ports:
            reject(
                "analysis.port.unknown",
                f"excitation port {excitation.port!r} is absent",
            )
        if excitation.mode_order not in capabilities.mode_orders:
            reject(
                "analysis.mode.unsupported",
                f"backend cannot launch mode {excitation.mode_order}",
            )
        if excitation.center_dbu is not None:
            (x, y) = excitation.center_dbu
            (x0, y0), (x1, y1) = description.domain.lo, description.domain.hi
            if not (x0 < x < x1 and y0 < y < y1):
                reject(
                    "analysis.excitation.outside_domain",
                    "point excitation must lie inside the domain",
                )
        if (
            isinstance(description, Reduced2D)
            and reductions
            and excitation.wavelength_pm != reductions[0].wavelength_pm
        ):
            reject(
                "analysis.reduction.wavelength",
                "excitation wavelength differs from the 2D reduction wavelength",
            )
    for observable in request.observables:
        if observable.kind not in capabilities.observables:
            reject(
                "analysis.observable.unsupported",
                f"backend cannot acquire {observable.kind}",
            )
        if observable.port is not None and observable.port not in ports:
            reject(
                "analysis.port.unknown",
                f"observable port {observable.port!r} is absent",
            )
        if (
            observable.kind is ObservableKind.RAW_FIELD
            and observable.field_component not in capabilities.field_components
        ):
            reject(
                "analysis.field.unsupported",
                f"backend cannot acquire {observable.field_component}",
            )
        if observable.kind in {
            ObservableKind.SOURCE_NORMALIZED_FLUX,
            ObservableKind.MODAL_SCATTERING,
        }:
            source = request.excitations[observable.source_index or 0]
            if source.kind != "port_mode":
                reject(
                    "analysis.normalization.source",
                    "normalized output requires a port-mode source",
                )
            if observable.port is None:
                reject(
                    "analysis.normalization.port",
                    "normalized output requires a measurement port",
                )
            if mapping is None:
                reject(
                    "analysis.normalization.mapping",
                    "normalized output requires an exact view mapping",
                )
            else:
                bound_ports = {binding.simulation_port for binding in mapping.bindings}
                if source.port not in bound_ports or observable.port not in bound_ports:
                    reject(
                        "analysis.mapping.port",
                        "source and measurement ports must be mapped",
                    )
                selected = [
                    binding
                    for binding in mapping.bindings
                    if binding.simulation_port in {source.port, observable.port}
                ]
                if any(
                    binding.reference_plane_offset_dbu != 0
                    or binding.mode != 0
                    or binding.modal_transform is not ModalTransform.IDENTITY
                    or binding.normalization is not Normalization.POWER_WAVE
                    for binding in selected
                ):
                    reject(
                        "analysis.mapping.convention",
                        "normalization requires zero-offset mode-0 identity power-wave bindings",
                    )
                if observable.kind is ObservableKind.MODAL_SCATTERING:
                    pair = (observable.output_channel, observable.excitation_channel)
                    source_binding = next(
                        (
                            b
                            for b in mapping.bindings
                            if b.simulation_port == source.port
                        ),
                        None,
                    )
                    output_binding = next(
                        (
                            b
                            for b in mapping.bindings
                            if b.simulation_port == observable.port
                        ),
                        None,
                    )
                    if (
                        source_binding is None
                        or output_binding is None
                        or pair
                        != (output_binding.model_channel, source_binding.model_channel)
                    ):
                        reject(
                            "analysis.mapping.channel",
                            "scattering channels do not match the selected source and measurement ports",
                        )
                    if pair not in {
                        (r.output, r.excitation) for r in mapping.responses
                    }:
                        reject(
                            "analysis.mapping.response",
                            "scattering pair is absent from the mapping",
                        )
    return tuple(errors)


def _expected_units(problem: PhysicalProblem, observable: Observable) -> str:
    if observable.kind is ObservableKind.RAW_FIELD:
        return "V/m" if observable.field_component in {"Ex", "Ey", "Ez"} else "A/m"
    if observable.kind is ObservableKind.RAW_FLUX:
        return "W/m" if isinstance(problem.description, Reduced2D) else "W"
    return "1"


def extract(
    problem: PhysicalProblem, request: AnalysisRequest, run: BackendRun
) -> tuple[tuple[Acquisition, ...], tuple[str, ...], tuple[Diagnostic, ...]]:
    """Accept only correctly typed requested acquisitions; never relabel raw data."""
    expected = {o.name: o for o in request.observables}
    accepted: dict[str, Acquisition] = {}
    diagnostics: list[Diagnostic] = []
    for acquisition in run.acquisitions:
        if acquisition.name not in expected:
            diagnostics.append(
                _diagnostic(
                    "analysis.output.unrequested",
                    f"backend returned unrequested {acquisition.name!r}",
                    request,
                )
            )
        elif acquisition.name in accepted:
            diagnostics.append(
                _diagnostic(
                    "analysis.output.duplicate",
                    f"backend returned duplicate {acquisition.name!r}",
                    request,
                )
            )
        elif acquisition.kind is not expected[acquisition.name].kind:
            diagnostics.append(
                _diagnostic(
                    "analysis.output.kind",
                    f"backend returned {acquisition.kind} for {acquisition.name!r}; expected {expected[acquisition.name].kind}",
                    request,
                )
            )
        elif acquisition.units != _expected_units(problem, expected[acquisition.name]):
            diagnostics.append(
                _diagnostic(
                    "analysis.output.units",
                    f"backend returned incompatible units {acquisition.units!r} for {acquisition.name!r}",
                    request,
                )
            )
        elif acquisition.kind is ObservableKind.RAW_FIELD and not acquisition.shape:
            diagnostics.append(
                _diagnostic(
                    "analysis.output.shape",
                    f"raw field {acquisition.name!r} needs a sampled axis",
                    request,
                )
            )
        else:
            accepted[acquisition.name] = acquisition
    acquired = tuple(accepted[name] for name in expected if name in accepted)
    missing = tuple(name for name in expected if name not in accepted)
    return acquired, missing, tuple(diagnostics)


def analyze(
    problem: PhysicalProblem,
    request: AnalysisRequest,
    backend: ElectromagneticBackend,
    mapping: ViewMapping | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> AnalysisOutcome:
    """Run only after preflight; preserve acquired data and exact settings on all outcomes."""
    capabilities = backend.capabilities()
    diagnostics = preflight(
        problem,
        request,
        mapping,
        capabilities,
        backend,
        progress=progress is not None,
        cancellation=cancellation is not None,
    )
    all_names = tuple(o.name for o in request.observables)
    if has_errors(diagnostics):
        return AnalysisOutcome(
            RunState.REJECTED,
            (),
            all_names,
            0,
            capabilities.backend,
            capabilities.version,
            request,
            diagnostics,
        )
    if cancellation is not None and cancellation.cancelled():
        return AnalysisOutcome(
            RunState.CANCELLED,
            (),
            all_names,
            0,
            capabilities.backend,
            capabilities.version,
            request,
            diagnostics,
        )
    run = backend.execute(problem, request, progress, cancellation)
    acquired, missing, extraction_diagnostics = extract(problem, request, run)
    all_diagnostics = (*diagnostics, *run.diagnostics, *extraction_diagnostics)
    state = run.state
    if run.completed_steps < 0 or run.completed_steps > request.numerical.steps:
        all_diagnostics = (
            *all_diagnostics,
            _diagnostic(
                "analysis.output.steps",
                "backend completed-step count is outside the request",
                request,
            ),
        )
    if state is RunState.COMPLETE and (
        missing
        or has_errors(all_diagnostics)
        or run.completed_steps != request.numerical.steps
    ):
        state = RunState.PARTIAL
        if run.completed_steps != request.numerical.steps and not any(
            d.code == "analysis.output.steps" for d in all_diagnostics
        ):
            all_diagnostics = (
                *all_diagnostics,
                _diagnostic(
                    "analysis.output.steps",
                    "backend reported complete before the requested steps",
                    request,
                ),
            )
    return AnalysisOutcome(
        state,
        acquired,
        missing,
        run.completed_steps,
        capabilities.backend,
        capabilities.version,
        request,
        all_diagnostics,
    )
