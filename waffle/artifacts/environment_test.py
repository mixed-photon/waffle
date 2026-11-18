from __future__ import annotations

import sys

from waffle.artifacts.environment import capture_environment


def test_records_python_platform_and_package_versions() -> None:
    env = capture_environment(["pytest", "no_such_package_xyz"])
    assert env["python"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert env["platform"] and env["machine"]
    assert env["package.pytest"][0].isdigit()
    assert env["package.no_such_package_xyz"] == "not-installed"
