"""Versioned local process protocol. Solver-neutral, numeric payloads use .npy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from waffle.contracts.analysis.request import AnalysisRequest, ObservableKind
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.simulation.common.backend import Acquisition, BackendRun, RunState


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    problem: PhysicalProblem
    request: AnalysisRequest


class ArrayOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    kind: ObservableKind
    file: str
    units: str
    axes: tuple[str, ...]
    shape: tuple[int, ...]


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    state: RunState
    completed_steps: StrictInt = Field(ge=0)
    diagnostics: tuple[Diagnostic, ...] = ()
    arrays: tuple[ArrayOutput, ...] = ()
    effective: dict[str, Any] = {}


def write_response(directory: Path, run: BackendRun, effective: dict[str, Any]) -> None:
    arrays = []
    for index, a in enumerate(run.acquisitions):
        name = f"array_{index}.npy"
        with (directory / name).open("xb") as stream:
            np.save(stream, np.asarray(a.payload), allow_pickle=False)
        arrays.append(
            ArrayOutput(
                name=a.name,
                kind=a.kind,
                file=name,
                units=a.units,
                axes=a.axes,
                shape=a.shape,
            )
        )
    response = WorkerResponse(
        state=run.state,
        completed_steps=run.completed_steps,
        diagnostics=run.diagnostics,
        arrays=tuple(arrays),
        effective=effective,
    )
    temporary = directory / "response.pending"
    with temporary.open("x") as stream:
        stream.write(response.model_dump_json())
    os.link(temporary, directory / "response.json")
    temporary.unlink()


def read_response(directory: Path) -> tuple[BackendRun, dict[str, Any]]:
    response = WorkerResponse.model_validate_json(
        (directory / "response.json").read_text()
    )
    acquisitions = []
    for item in response.arrays:
        if Path(item.file).name != item.file or not item.file.endswith(".npy"):
            raise ValueError("invalid worker array path")
        array = np.load(directory / item.file, allow_pickle=False)
        acquisitions.append(
            Acquisition(item.name, item.kind, array, item.units, item.axes, item.shape)
        )
    return BackendRun(
        response.state,
        tuple(acquisitions),
        response.completed_steps,
        response.diagnostics,
    ), response.effective
