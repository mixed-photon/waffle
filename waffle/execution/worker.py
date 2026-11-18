"""Controller for isolated solver processes and immutable attempt publication."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.analysis.request import AnalysisRequest
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.attempt import AttemptState
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.execution.protocol import WorkerRequest, read_response
from waffle.simulation.common.backend import BackendRun, CancellationToken, RunState


@dataclass(frozen=True)
class ExecutionResult:
    attempt_id: str
    directory: Path
    run: BackendRun
    artifact: ArtifactRef | None
    effective: dict[str, Any]


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Stop the worker's private process group even if it exits during cancellation."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_isolated(
    problem: PhysicalProblem,
    request: AnalysisRequest,
    *,
    command: tuple[str, ...],
    root: Path,
    store: ArtifactStore,
    cancellation: CancellationToken | None = None,
) -> ExecutionResult:
    """Retain a unique work directory for every attempt; publish complete results only.

    command receives a single absolute work-directory argument. Timeout/cancellation
    terminates the process group, including an in-progress compile. Retry is a new
    attempt; neither response files nor immutable completed artifacts are overwritten.
    """
    wire = WorkerRequest(problem=problem, request=request)
    operation = OperationInfo(
        name="simulation.isolated",
        version="1",
        parameters_digest=content_hash(wire.model_dump(mode="json")),
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "command": repr(command),
        },
    )
    attempt = store.start_attempt(
        operation,
        (
            InputLink(role="physical_problem", target=revision_of(problem)),
            InputLink(role="analysis_request", target=revision_of(request)),
        ),
    )
    attempt_id = attempt.start.attempt_id
    directory = root.resolve() / attempt_id
    artifact = None
    effective: dict[str, Any] = {}
    run = BackendRun(RunState.FAILED, (), 0)
    process: subprocess.Popen[bytes] | None = None
    directory_created = False
    try:
        directory.mkdir(parents=True, exist_ok=False)
        directory_created = True
        (directory / "request.json").write_text(
            wire.model_dump_json(), encoding="utf-8"
        )
        if cancellation is not None and cancellation.cancelled():
            run = BackendRun(RunState.CANCELLED, (), 0)
        else:
            environment = os.environ.copy()
            environment.update(
                JAX_PLATFORMS="cpu", OMP_NUM_THREADS=str(request.resources.cpu_cores)
            )
            with (
                (directory / "stdout.log").open("xb") as stdout,
                (directory / "stderr.log").open("xb") as stderr,
            ):
                process = subprocess.Popen(
                    (*command, str(directory)),
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    start_new_session=True,
                )
                deadline = time.monotonic() + request.resources.wall_time_s
                while process.poll() is None:
                    cancelled = cancellation is not None and cancellation.cancelled()
                    timeout = time.monotonic() >= deadline
                    if cancelled or timeout:
                        _terminate(process)
                        diagnostic = Diagnostic(
                            severity=Severity.ERROR,
                            code="execution.cancelled"
                            if cancelled
                            else "execution.timeout",
                            message="worker cancelled"
                            if cancelled
                            else "worker exceeded wall-time budget",
                        )
                        run = BackendRun(
                            RunState.CANCELLED if cancelled else RunState.FAILED,
                            (),
                            0,
                            (diagnostic,),
                        )
                        break
                    time.sleep(0.05)
                else:
                    if process.returncode:
                        raise ValueError(
                            f"worker exited with code {process.returncode}; inspect stderr.log"
                        )
                    run, effective = read_response(directory)
            if run.state is RunState.COMPLETE:
                if run.completed_steps != request.numerical.steps or {
                    a.name for a in run.acquisitions
                } != {o.name for o in request.observables}:
                    raise ValueError(
                        "worker complete response does not cover requested steps and observables"
                    )
                if len(run.acquisitions) != len(request.observables) or has_errors(
                    run.diagnostics
                ):
                    raise ValueError(
                        "worker complete response has duplicate outputs or error diagnostics"
                    )
                requested = {o.name: o for o in request.observables}
                for acquired in run.acquisitions:
                    observable = requested[acquired.name]
                    expected_units = (
                        ("V/m" if observable.field_component.startswith("E") else "A/m")
                        if observable.kind.value == "raw_field"
                        else (
                            "W/m" if problem.description.dimensionality == "2d" else "W"
                        )
                        if observable.kind.value == "raw_flux"
                        else "1"
                    )
                    if (
                        acquired.kind != observable.kind
                        or acquired.units != expected_units
                    ):
                        raise ValueError(
                            "worker acquisition kind/units disagree with request"
                        )
                writer = store.open_writer(
                    attempt_id,
                    "simulation_fields",
                    {
                        "schema_version": 1,
                        "backend": request.backend_settings.backend,
                        "effective": effective,
                    },
                )
                writer.write_json("request.json", wire.model_dump(mode="json"))
                writer.write_bytes(
                    "response.json",
                    (directory / "response.json").read_bytes(),
                    "application/json",
                )
                for a in run.acquisitions:
                    writer.write_array(f"{a.name}.npy", a.payload)
                artifact = writer.commit()
    except ArtifactError as exc:
        run = BackendRun(RunState.FAILED, (), 0, exc.diagnostics)
    except (OSError, ValueError, RuntimeError) as exc:
        run = BackendRun(
            RunState.FAILED,
            (),
            0,
            (
                Diagnostic(
                    severity=Severity.ERROR,
                    code="execution.worker_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            ),
        )
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate(process)
        store.finish_attempt(attempt_id, AttemptState.CANCELLED)
        raise
    state = (
        AttemptState.SUCCEEDED
        if run.state is RunState.COMPLETE
        else AttemptState.CANCELLED
        if run.state is RunState.CANCELLED
        else AttemptState.FAILED
    )
    if directory_created:
        try:
            with (directory / "controller_outcome.json").open(
                "x", encoding="utf-8"
            ) as output:
                json.dump(
                    {
                        "schema_version": 1,
                        "state": run.state.value,
                        "diagnostics": [
                            d.model_dump(mode="json") for d in run.diagnostics
                        ],
                        "artifact": str(artifact) if artifact else None,
                    },
                    output,
                )
        except OSError as exc:
            diagnostic = Diagnostic(
                severity=Severity.WARNING,
                code="execution.log_unavailable",
                message=str(exc),
            )
            run = BackendRun(
                run.state,
                run.acquisitions,
                run.completed_steps,
                (*run.diagnostics, diagnostic),
            )
    store.finish_attempt(
        attempt_id, state, run.diagnostics, (artifact,) if artifact else ()
    )
    return ExecutionResult(attempt_id, directory, run, artifact, effective)
