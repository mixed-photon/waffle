"""The default test environment must not contain any optional backend."""

from __future__ import annotations

import importlib.util

import pytest


@pytest.mark.parametrize("module", ["gdsfactory", "beamz"])
def test_backend_not_importable_in_core_environment(module: str) -> None:
    assert importlib.util.find_spec(module) is None
