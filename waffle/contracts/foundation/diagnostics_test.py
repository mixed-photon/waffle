from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors


def make(severity: Severity = Severity.ERROR, **kwargs: object) -> Diagnostic:
    fields: dict[str, object] = {"code": "units.unknown", "message": "bad unit"}
    fields.update(kwargs)
    return Diagnostic(severity=severity, **fields)  # type: ignore[arg-type]


def test_str_with_subject_and_location() -> None:
    d = make(subject="component:wg", location=("parameters", "taper", 2, "width"))
    assert str(d) == "error [units.unknown] component:wg.parameters.taper[2].width: bad unit"


def test_str_without_subject() -> None:
    assert str(make(Severity.WARNING)) == "warning [units.unknown]: bad unit"
    assert str(make(location=("a", 0))) == "error [units.unknown] a[0]: bad unit"


def test_json_round_trip() -> None:
    d = make(subject="model:m", location=("x", 1))
    assert Diagnostic.model_validate_json(d.model_dump_json()) == d
    assert d.model_dump(mode="json")["severity"] == "error"


@pytest.mark.parametrize("code", ["", "nodot", "Units.Bad", "units.", ".units"])
def test_code_must_be_dotted_lowercase(code: str) -> None:
    with pytest.raises(ValidationError):
        make(code=code)


def test_message_is_required() -> None:
    with pytest.raises(ValidationError):
        make(message="")


def test_diagnostics_are_immutable() -> None:
    with pytest.raises(ValidationError):
        make().message = "other"  # type: ignore[misc]


def test_has_errors() -> None:
    assert not has_errors([])
    assert not has_errors([make(Severity.INFO), make(Severity.WARNING)])
    assert has_errors([make(Severity.INFO), make(Severity.ERROR)])


def test_code_pattern_is_anchored() -> None:
    # pydantic patterns are searches unless anchored; a partial match must not pass.
    for bad in ("Units.bad", "units.bad code", "units", "x units.bad"):
        with pytest.raises(ValidationError):
            Diagnostic(severity=Severity.ERROR, code=bad, message="m")
