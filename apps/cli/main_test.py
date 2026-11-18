from __future__ import annotations

import pytest

from apps.cli import main as cli
from waffle import __version__
from waffle.contracts.foundation.diagnostics import Severity


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"waffle {__version__}"


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2


def test_doctor_reports_installed_and_missing_backends() -> None:
    found = {
        d.subject: d for d in cli.doctor(("pytest", "no_such_backend")) if d.subject
    }
    assert found["pytest"].code == "doctor.backend_available"
    assert found["no_such_backend"].code == "doctor.backend_missing"
    assert found["no_such_backend"].severity is Severity.INFO


def test_doctor_flags_unsupported_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "version_info", (3, 11, 0))
    assert [d.code for d in cli.doctor(())] == [
        "doctor.platform",
        "doctor.python_version",
    ]


def test_doctor_command_succeeds_without_backends(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "doctor.platform" in out
    assert "gdsfactory" in out and "beamz" in out
