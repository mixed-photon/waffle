"""Describe the runtime environment for `OperationInfo.environment`."""

from __future__ import annotations

import platform
from collections.abc import Iterable
from importlib import metadata


def capture_environment(packages: Iterable[str] = ()) -> dict[str, str]:
    """Python version, platform, and the installed versions of `packages`.

    A package that is not installed is recorded as `not-installed`.
    """
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    for name in packages:
        try:
            env[f"package.{name}"] = metadata.version(name)
        except metadata.PackageNotFoundError:
            env[f"package.{name}"] = "not-installed"
    return env
