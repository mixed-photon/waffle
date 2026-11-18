"""Small deterministic backend for orchestration tests; never imports numerics."""

from __future__ import annotations

from dataclasses import dataclass, field

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    ObservableKind,
)
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import BoundaryKind, PhysicalProblem
from waffle.simulation.common.backend import (
    BackendCapabilities,
    BackendRun,
    CancellationToken,
    ProgressCallback,
)


@dataclass
class FakeBackend:
    next_run: BackendRun
    calls: int = 0
    observed_settings: BackendSettings | None = None
    capabilities_value: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(
            backend="fake",
            version="1.0",
            dimensionalities=frozenset({"2d", "3d"}),
            polarizations=frozenset({Polarization.TE, Polarization.TM}),
            excitation_kinds=frozenset({"point", "port_mode"}),
            mode_orders=frozenset({0}),
            observables=frozenset({ObservableKind.RAW_FIELD, ObservableKind.RAW_FLUX}),
            boundaries=frozenset({BoundaryKind.ABSORBING}),
            field_components=frozenset({"Ez", "Hz"}),
            settings_versions=frozenset({1}),
            supports_progress=True,
            supports_cancellation=True,
        )
    )

    def capabilities(self) -> BackendCapabilities:
        return self.capabilities_value

    def validate_settings(self, settings: BackendSettings) -> tuple[Diagnostic, ...]:
        if settings.parameters:
            return (
                Diagnostic(
                    severity=Severity.ERROR,
                    code="analysis.backend.settings",
                    message="fake backend accepts an empty settings payload only",
                ),
            )
        return ()

    def execute(
        self,
        problem: PhysicalProblem,
        request: AnalysisRequest,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> BackendRun:
        del problem, cancellation
        self.calls += 1
        self.observed_settings = request.backend_settings
        if progress is not None:
            from waffle.simulation.common.backend import ProgressEvent

            progress(
                ProgressEvent(
                    self.next_run.completed_steps, request.numerical.steps, "execute"
                )
            )
        return self.next_run

    def validate_problem(
        self, problem: PhysicalProblem, request: AnalysisRequest
    ) -> tuple[Diagnostic, ...]:
        del problem, request
        return ()
