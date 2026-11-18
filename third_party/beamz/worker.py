"""Installed and Bazel entry point for the version-1 isolated solver protocol."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

from third_party.beamz.adapter import BeamZBackend
from waffle.execution.protocol import WorkerRequest, write_response


def main() -> None:
    directory = Path(sys.argv[1]).resolve()
    wire = WorkerRequest.model_validate_json((directory / "request.json").read_text())
    environment = {
        "schema_version": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "JAX_PLATFORMS": os.environ.get("JAX_PLATFORMS"),
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "packages": {},
    }
    for package in ("beamz", "jax", "numpy"):
        try:
            environment["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            environment["packages"][package] = "unavailable"
    with (directory / "worker_environment.json").open("x") as stream:
        json.dump(environment, stream)
    backend = BeamZBackend()
    run = backend.execute(wire.problem, wire.request)
    write_response(directory, run, backend.last_metadata)


if __name__ == "__main__":
    main()
