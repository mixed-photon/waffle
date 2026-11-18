from __future__ import annotations

import base64
import io
import json
import sys
import threading
from pathlib import Path

import numpy as np

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
)
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.execution.worker import run_isolated


def fixture() -> tuple[PhysicalProblem, AnalysisRequest]:
    problem = PhysicalProblem.model_validate(
        {
            "id": "physical_problem:test",
            "layout": "layout:test@sha256:" + "0" * 64,
            "technology": "technology:test@sha256:" + "0" * 64,
            "top_cell": "test",
            "dbu_pm": 1000,
            "description": {
                "dimensionality": "2d",
                "domain": {
                    "lo": [0, 0],
                    "hi": [10000, 10000],
                    "boundaries": {
                        f: {"kind": "absorbing", "thickness_pm": 1000000}
                        for f in ("x_min", "x_max", "y_min", "y_max")
                    },
                },
                "background_index": 1.0,
                "regions": [],
            },
            "approximations": [
                {
                    "type": "effective_index",
                    "method": "test",
                    "method_version": 1,
                    "polarization": "te",
                    "mode_order": 0,
                    "wavelength_pm": 1550000,
                    "window_z_min_pm": 0,
                    "window_z_max_pm": 1000000,
                    "validity": "test fixture",
                    "columns": [
                        {
                            "layer": None,
                            "segments": [
                                {
                                    "z_min_pm": 0,
                                    "z_max_pm": 1000000,
                                    "material": "vacuum",
                                    "index": 1.0,
                                }
                            ],
                            "n_eff": 1.0,
                            "rule": "cladding_cutoff",
                        }
                    ],
                }
            ],
        }
    )
    request = AnalysisRequest(
        id="analysis_request:test",
        problem=revision_of(problem),
        polarization="te",
        excitations=(
            Excitation(kind="point", center_dbu=(1000, 0), wavelength_pm=1550000),
        ),
        observables=(Observable(name="field", kind="raw_field", field_component="Hz"),),
        numerical=NumericalSettings(
            grid_spacing_pm=100000, time_step_as=100, steps=100
        ),
        backend_settings=BackendSettings(backend="fake", version="1", schema_version=1),
    )
    return problem, request


def test_success_retry_failure_cancel_and_protocol(tmp_path: Path) -> None:
    problem, request = fixture()
    store = LocalArtifactStore(tmp_path / "store")
    buffer = io.BytesIO()
    np.save(buffer, np.array([1.0, 2.0]), allow_pickle=False)
    response = {
        "schema_version": 1,
        "state": "complete",
        "completed_steps": 100,
        "diagnostics": [],
        "arrays": [
            {
                "name": "field",
                "kind": "raw_field",
                "file": "array_0.npy",
                "units": "A/m",
                "axes": ["sample"],
                "shape": [2],
            }
        ],
        "effective": {"fake": "1"},
    }
    script = tmp_path / "worker.py"
    script.write_text(
        "import pathlib,sys,json,base64\np=pathlib.Path(sys.argv[1])\n(p/'array_0.npy').write_bytes(base64.b64decode("
        + repr(base64.b64encode(buffer.getvalue()).decode())
        + "))\n(p/'response.json').write_text("
        + repr(json.dumps(response))
        + ")\n"
    )
    kwargs = {
        "command": (sys.executable, str(script)),
        "root": tmp_path / "work",
        "store": store,
    }
    first = run_isolated(problem, request, **kwargs)
    second = run_isolated(problem, request, **kwargs)
    assert first.artifact == second.artifact
    assert first.attempt_id != second.attempt_id
    assert first.directory.exists() and second.directory.exists()
    assert len(store.list_attempts()) == 2
    original = store.read_bytes(first.artifact, "response.json")
    script.write_text("raise RuntimeError('expected worker failure')")
    failed = run_isolated(problem, request, **kwargs)
    assert failed.run.state.value == "failed" and failed.artifact is None
    assert "expected worker failure" in (failed.directory / "stderr.log").read_text()

    class Token:
        def __init__(self) -> None:
            self.event = threading.Event()

        def cancelled(self) -> bool:
            return self.event.is_set()

    token = Token()
    script.write_text("import time\ntime.sleep(30)\n")
    timer = threading.Timer(0.2, token.event.set)
    timer.start()
    cancelled = run_isolated(problem, request, cancellation=token, **kwargs)
    timer.join()
    assert cancelled.run.state.value == "cancelled" and cancelled.artifact is None
    assert store.read_bytes(first.artifact, "response.json") == original
    assert len(store.list_attempts()) == 4
    assert (cancelled.directory / "request.json").exists()
    assert (cancelled.directory / "controller_outcome.json").exists()


def test_rejects_invalid_response_and_enforces_timeout(tmp_path: Path) -> None:
    problem, request = fixture()
    store = LocalArtifactStore(tmp_path / "store")
    script = tmp_path / "worker.py"
    script.write_text(
        'import pathlib,sys\n(pathlib.Path(sys.argv[1])/\'response.json\').write_text(\'{"schema_version":2,"state":"complete","completed_steps":100}\')\n'
    )
    kwargs = {
        "command": (sys.executable, str(script)),
        "root": tmp_path / "work",
        "store": store,
    }
    invalid = run_isolated(problem, request, **kwargs)
    assert invalid.run.state.value == "failed" and invalid.artifact is None
    script.write_text("import time\ntime.sleep(30)\n")
    bounded = request.model_copy(
        update={"resources": request.resources.model_copy(update={"wall_time_s": 1})}
    )
    timed_out = run_isolated(problem, bounded, **kwargs)
    assert timed_out.run.state.value == "failed"
    assert timed_out.run.diagnostics[0].code == "execution.timeout"
    assert timed_out.artifact is None


def test_response_publication_cannot_overwrite(tmp_path: Path) -> None:
    import pytest

    from waffle.execution.protocol import write_response
    from waffle.simulation.common.backend import BackendRun, RunState

    run = BackendRun(RunState.REJECTED, (), 0)
    write_response(tmp_path, run, {"original": True})
    original = (tmp_path / "response.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_response(tmp_path, run, {"original": False})
    assert (tmp_path / "response.json").read_bytes() == original


def test_unavailable_work_directory_finishes_attempt_as_failed(tmp_path: Path) -> None:
    problem, request = fixture()
    store = LocalArtifactStore(tmp_path / "store")
    root = tmp_path / "not_a_directory"
    root.write_text("unavailable", encoding="utf-8")
    run = run_isolated(
        problem, request, command=(sys.executable,), root=root, store=store
    )
    attempt = store.get_attempt(run.attempt_id)
    assert run.run.state.value == "failed" and run.artifact is None
    assert attempt.outcome is not None and attempt.outcome.state.value == "failed"
    assert attempt.outcome.diagnostics[0].code == "execution.worker_failed"
