"""A value or the diagnostics explaining why there is none."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from waffle.contracts.foundation.diagnostics import Diagnostic, has_errors


@dataclass(frozen=True)
class Result[T]:
    """`value` is None exactly when `diagnostics` contains an error.

    Warnings and infos may accompany a value.
    """

    value: T | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None

    @classmethod
    def of(cls, value: T, diagnostics: Iterable[Diagnostic] = ()) -> Result[T]:
        """Succeed with `value` unless `diagnostics` holds an error."""
        found = tuple(diagnostics)
        return cls(None if has_errors(found) else value, found)

    def unwrap(self) -> T:
        if self.value is None:
            raise ValueError("; ".join(str(d) for d in self.diagnostics) or "no value")
        return self.value
