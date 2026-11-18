"""Build the local Waffle and independently installable demonstration wheels."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("dist/wheels"))
    args = parser.parse_args(argv)
    uv = shutil.which("uv")
    if uv is None:
        parser.error("install uv to build the local wheels")
    workspace = Path(
        os.environ.get("BUILD_WORKSPACE_DIRECTORY", Path(__file__).resolve().parents[1])
    )
    output = workspace / args.out
    projects = (
        workspace,
        *(
            workspace / "distributions" / package
            for package in (
                "waffle-pdk-demo",
                "waffle-example-waveguide",
                "waffle-example-mmi",
            )
        ),
    )
    for project in projects:
        built = subprocess.run(
            (uv, "build", "--wheel", "--out-dir", str(output), str(project)),
            check=False,
        )
        if built.returncode:
            return built.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
