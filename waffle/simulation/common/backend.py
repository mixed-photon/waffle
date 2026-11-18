"""Solver-facing interface. Adapters implement this without entering core imports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    ObservableKind,
)
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import BoundaryKind, PhysicalProblem


@dataclass(frozen=True)
class BackendCapabilities:
    backend: str
    version: str
    dimensionalities: frozenset[str]
    polarizations: frozenset[Polarization]
    excitation_kinds: frozenset[str]
    mode_orders: frozenset[int]
    observables: frozenset[ObservableKind]
    boundaries: frozenset[BoundaryKind]
    field_components: frozenset[str]
    settings_versions: frozenset[int]
    supports_progress: bool = False
    supports_cancellation: bool = False


@dataclass(frozen=True)
class ProgressEvent:
    completed_steps: int
    total_steps: int
    phase: str


type ProgressCallback = Callable[[ProgressEvent], None]


class CancellationToken(Protocol):
    def cancelled(self) -> bool: ...


class RunState(StrEnum):
    REJECTED = "rejected"
    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Acquisition:
    """One measured observable. The kind is intrinsic, not an extraction label."""

    name: str
    kind: ObservableKind
    payload: object
    units: str
    axes: tuple[str, ...]
    shape: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.payload is None or not self.units or len(self.axes) != len(self.shape):
            raise ValueError(
                "acquisition needs payload, units, and matching axes/shape"
            )
        if any(not axis for axis in self.axes) or len(set(self.axes)) != len(self.axes):
            raise ValueError("acquisition axes must be named and unique")
        if any(size <= 0 for size in self.shape):
            raise ValueError("acquisition dimensions must be nonempty")
        try:
            array = np.asarray(self.payload)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "acquisition payload must be a rectangular numeric array"
            ) from exc
        if (
            array.dtype.kind not in "iufc"
            or array.size == 0
            or tuple(array.shape) != self.shape
        ):
            raise ValueError("acquisition payload needs numeric values matching shape")
        if not bool(np.isfinite(array).all()):
            raise ValueError("acquisition payload must be finite")


@dataclass(frozen=True)
class BackendRun:
    state: RunState
    acquisitions: tuple[Acquisition, ...]
    completed_steps: int
    diagnostics: tuple[Diagnostic, ...] = ()


class ElectromagneticBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...

    def validate_settings(
        self, settings: BackendSettings
    ) -> tuple[Diagnostic, ...]: ...

    def validate_problem(
        self, problem: PhysicalProblem, request: AnalysisRequest
    ) -> tuple[Diagnostic, ...]: ...

    def execute(
        self,
        problem: PhysicalProblem,
        request: AnalysisRequest,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> BackendRun: ...
