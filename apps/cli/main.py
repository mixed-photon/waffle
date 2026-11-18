"""The headless Waffle command line."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path

from waffle import __version__
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors

MIN_PYTHON = (3, 12)
OPTIONAL_BACKENDS = ("gdsfactory", "beamz")


def doctor(
    backends: Sequence[str] = OPTIONAL_BACKENDS, *, required: Sequence[str] = ()
) -> list[Diagnostic]:
    """Preflight report. A missing backend is an error when explicitly required.

    Backends are located with `find_spec`, so none of them is imported.
    """
    found = [
        Diagnostic(
            severity=Severity.INFO,
            code="doctor.platform",
            message=f"Python {platform.python_version()} on {platform.system()} {platform.machine()}",
        )
    ]
    if sys.version_info < MIN_PYTHON:
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="doctor.python_version",
                message=f"Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} is required",
            )
        )
    for name in backends:
        if importlib.util.find_spec(name) is None:
            found.append(
                Diagnostic(
                    severity=Severity.ERROR if name in required else Severity.INFO,
                    code="doctor.backend_missing",
                    subject=name,
                    message=f"not installed (optional; install with `pip install waffle-eda[{name}]`)",
                )
            )
            continue
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "unknown version"
        found.append(
            Diagnostic(
                severity=Severity.INFO,
                code="doctor.backend_available",
                subject=name,
                message=f"installed ({version})",
            )
        )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waffle", description="Waffle command line.")
    parser.add_argument("--version", action="version", version=f"waffle {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser(
        "doctor", help="check the Python environment and backends"
    )
    preflight.add_argument(
        "--require", choices=OPTIONAL_BACKENDS, action="append", default=[]
    )
    reference = commands.add_parser(
        "reference", help="run the installed waveguide reference workflow"
    )
    reference.add_argument(
        "--store",
        type=Path,
        required=True,
        help="directory for attempts and immutable artifacts",
    )
    reference.add_argument(
        "--model", choices=("analytic", "simulated", "calibrated"), default="analytic"
    )
    workers = reference.add_mutually_exclusive_group()
    workers.add_argument(
        "--beamz-python",
        type=Path,
        help="Python executable in an installed BeamZ worker environment",
    )
    workers.add_argument(
        "--beamz-worker",
        type=Path,
        help="BeamZ worker executable (for example, the Bazel binary)",
    )
    replay = commands.add_parser(
        "replay", help="reproduce a reference experiment from a pinned binding"
    )
    replay.add_argument("--store", type=Path, required=True)
    replay.add_argument(
        "binding", help="experiment_binding@sha256:… reference printed by reference"
    )
    args = parser.parse_args(argv)

    if args.command == "doctor":
        diagnostics = doctor(required=args.require)
        for diagnostic in diagnostics:
            print(diagnostic)
        return 1 if has_errors(diagnostics) else 0

    # The CLI's package is apps.cli in Bazel and waffle.apps.cli in the wheel.
    lifecycle = import_module(f"{__package__ or 'apps.cli'}.lifecycle")
    if args.command == "reference":
        executable = args.beamz_python or args.beamz_worker
        worker = None
        if executable is not None:
            # Keep the virtualenv executable path so Python finds its pyvenv.cfg.
            executable = executable.absolute()
            if not executable.is_file() or not os.access(executable, os.X_OK):
                reference.error(
                    f"worker executable is missing or not executable: {executable}"
                )
            worker = (
                (str(executable), "-m", "third_party.beamz.worker")
                if args.beamz_python
                else (str(executable),)
            )
        result = lifecycle.run_reference(
            args.store, choice=args.model, worker_command=worker
        )
        for diagnostic in result.diagnostics:
            print(diagnostic, file=sys.stderr)
        if result.value is None:
            return 1
        print(
            json.dumps(
                {"artifact": str(result.value.artifact), **result.value.report},
                indent=2,
            )
        )
        return 0

    from waffle.artifacts.local import LocalArtifactStore
    from waffle.contracts.results.artifact import ArtifactRef

    try:
        binding = ArtifactRef.parse(args.binding)
    except ValueError as exc:
        replay.error(str(exc))
    if not args.store.is_dir():
        replay.error(f"artifact store does not exist: {args.store}")
    result = lifecycle.reproduce_experiment(LocalArtifactStore(args.store), binding)
    for diagnostic in result.diagnostics:
        print(diagnostic, file=sys.stderr)
    if result.value is None:
        return 1
    print(
        json.dumps(
            {
                "binding": str(binding),
                "responses": [item.as_dict() for item in result.value],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
