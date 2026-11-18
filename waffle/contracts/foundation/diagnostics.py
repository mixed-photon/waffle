"""Structured diagnostics: machine-readable findings with a stable code.

Functions that validate or resolve return `Diagnostic`s instead of raising when
the caller can act on several problems at once.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Diagnostic(BaseModel):
    """One finding.

    `code` is a stable dotted identifier such as `units.unknown_dimension`.
    `subject` is the string form of the entity or artifact concerned, and
    `location` is the path inside it (field names and list indices).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Severity
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    message: str = Field(min_length=1)
    subject: str | None = None
    location: tuple[str | int, ...] = ()

    def __str__(self) -> str:
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in self.location)
        where = f"{self.subject or ''}{path}" if self.subject else path.lstrip(".")
        prefix = f"{self.severity.value} [{self.code}]"
        return f"{prefix} {where}: {self.message}" if where else f"{prefix}: {self.message}"


def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.severity is Severity.ERROR for d in diagnostics)
