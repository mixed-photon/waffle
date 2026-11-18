from __future__ import annotations

import pytest

from waffle.contracts.technology.versions import parse_specifier, parse_version, satisfies


def test_versions_compare_with_implicit_trailing_zeros() -> None:
    assert parse_version("1.2") == (1, 2)
    assert satisfies("1.2", "==1.2.0")
    assert satisfies("0.10.0", ">0.9.9")  # numeric, not lexicographic
    assert not satisfies("0.9", ">=0.10")


@pytest.mark.parametrize(
    ("version", "specifier", "expected"),
    [
        ("0.1.5", ">=0.1,<0.2", True),
        ("0.2.0", ">=0.1,<0.2", False),
        ("0.0.9", ">=0.1,<0.2", False),
        ("0.2.0", "<=0.2.0", True),
        ("0.2.0", "!=0.2.0", False),
        ("0.2.0", ">= 0.2 , < 1", True),
    ],
)
def test_specifier_clauses(version: str, specifier: str, expected: bool) -> None:
    assert satisfies(version, specifier) is expected


@pytest.mark.parametrize("text", ["", "1.x", "1.0rc1", "-1", "1..2", "٣"])
def test_unsupported_versions_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_version(text)


@pytest.mark.parametrize("text", ["", "1.0", "~=1.0", ">=1.0,", ">=1.*", "=>1"])
def test_unsupported_specifiers_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_specifier(text)
