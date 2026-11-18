"""Qualify both launch directions through the reference and replay entry points."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from apps.cli.main import main
from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.results.artifact import ArtifactRef


def test_directional_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    worker = Path("third_party/beamz/worker").resolve()
    store_path = root / "store"
    status = main(
        [
            "reference", "--model", "simulated", "--store", str(store_path),
            "--beamz-worker", str(worker),
        ]
    )
    captured = capsys.readouterr()
    assert status == 0, captured.err
    result = json.loads(captured.out)
    refs = result["references"]
    store = LocalArtifactStore(store_path)
    evidence_ref = ArtifactRef.parse(refs["transfer"])
    summary = json.loads(store.read_bytes(evidence_ref, "report.json"))
    (root / "directional.json").write_text(
        json.dumps({**summary, "artifact": str(evidence_ref)}, indent=2) + "\n",
        encoding="utf-8",
    )

    assert summary["passed"]
    assert set(summary["runs"]) == {"fine", "medium", "coarse", "duration", "boundary"}
    assert all(len(pair) == 2 for pair in summary["runs"].values())
    run_ids = {
        run["attempt_id"] for pair in summary["runs"].values() for run in pair
    }
    assert len(run_ids) == 10
    assert set(result["worker_attempts"]) == run_ids
    validation = json.loads(
        store.read_bytes(ArtifactRef.parse(refs["validation"]), "report.json")
    )
    assert validation["comparisons"] == result["numerical_comparisons"]
    assert len(validation["comparisons"]) == 48
    assert all(
        check["error"] <= check["tolerance"] for check in validation["comparisons"]
    )
    assert validation["uncomputed_channels"] == []

    binding = refs["selected_binding"]
    status = main(["replay", "--store", str(store_path), binding])
    captured = capsys.readouterr()
    assert status == 0, captured.err
    replay = json.loads(captured.out)
    assert replay["binding"] == binding
    assert replay["responses"] == result["replayed_responses"]
    (response,) = replay["responses"]
    matrix = summary["estimates"]["fine"]["matrix"]
    assert response["channels"] == [
        {
            "output": output,
            "excitation": excitation,
            "real": matrix[row][column][0],
            "imag": matrix[row][column][1],
        }
        for row, output in enumerate(("o1", "o2"))
        for column, excitation in enumerate(("o1", "o2"))
    ]
    assert {path.name for path in (store_path / "workers").iterdir()} == run_ids
