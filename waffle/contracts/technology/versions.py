"""Dotted-integer versions and a minimal specifier syntax.

A specifier is comma-separated clauses `<op><version>` with `op` one of `>=`, `>`, `<=`,
`<`, `==`, `!=`, e.g. `>=0.1,<0.2`. Versions are dot-separated non-negative integers
(`0.3`, `1.2.0`); missing trailing components count as zero. Pre-release, local, wildcard
and `~=` forms of PEP 440 are not supported and are rejected rather than guessed at.
"""

from __future__ import annotations

import operator
from collections.abc import Callable

type Version = tuple[int, ...]

_OPERATORS: dict[str, Callable[[Version, Version], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


def parse_version(text: str) -> Version:
    """Parse `1.2.0` (ValueError if it is not dot-separated non-negative integers)."""
    parts = text.strip().split(".")
    if not all(p.isascii() and p.isdigit() for p in parts):
        raise ValueError(f"unsupported version {text!r}: expected dotted integers")
    return tuple(int(p) for p in parts)


def _padded(a: Version, b: Version) -> tuple[Version, Version]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


def parse_specifier(text: str) -> tuple[tuple[str, Version], ...]:
    """Parse a specifier into `(operator, version)` clauses (ValueError if malformed)."""
    clauses: list[tuple[str, Version]] = []
    for raw in text.split(","):
        clause = raw.strip()
        op = next((o for o in _OPERATORS if clause.startswith(o)), None)
        if op is None:
            raise ValueError(f"unsupported specifier clause {clause!r}")
        clauses.append((op, parse_version(clause[len(op) :])))
    return tuple(clauses)


def satisfies(version: str, specifier: str) -> bool:
    """Whether `version` meets every clause of `specifier` (ValueError if either is malformed)."""
    found = parse_version(version)
    return all(_OPERATORS[op](*_padded(found, bound)) for op, bound in parse_specifier(specifier))
