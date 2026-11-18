"""Smoke test: the pinned upstream gdsfactory imports and matches requirements.in."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
from pathlib import Path

PACKAGE = "gdsfactory"


def pinned_version() -> str:
    for line in Path(os.environ["REQUIREMENTS_IN"]).read_text().splitlines():
        name, sep, version = line.partition("==")
        if sep and name.strip() == PACKAGE:
            return version.strip()
    raise AssertionError(f"{PACKAGE} is not pinned in requirements.in")


def test_imports_pinned_version() -> None:
    importlib.import_module(PACKAGE)
    assert importlib.metadata.version(PACKAGE) == pinned_version()
