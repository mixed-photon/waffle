"""Load PDKs and resolve exact technology revisions from them.

PDKs arrive by two routes that share one loader and validation (`PdkRegistry.add`):

* installed packages advertising an entry point in the group `waffle.pdks`; each entry
  point loads to a zero-argument callable returning a `Pdk` (`discover`);
* a directory in the layout of `waffle.pdk.directory` (`add_directory`), for local or
  restricted PDKs that are not installed.

Diagnostics: `pdk.entry_point_failed`, `pdk.entry_point_invalid`, `pdk.duplicate`, the
`waffle.pdk.directory` and `waffle.pdk.validate` codes, `technology.unknown` and
`technology.revision_unavailable` (from `resolve`).
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from waffle import __version__
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.technology.pdk import Pdk
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.pdk.directory import read_pdk
from waffle.pdk.validate import validate_pdk

ENTRY_POINT_GROUP = "waffle.pdks"


class EntryPointLike(Protocol):
    """The part of `importlib.metadata.EntryPoint` that discovery uses."""

    @property
    def name(self) -> str: ...

    def load(self) -> object: ...


class PdkRegistry:
    """The PDKs loaded so far and the technology revisions they provide."""

    def __init__(self, waffle_version: str = __version__) -> None:
        self._waffle_version = waffle_version
        self._pdks: dict[EntityId, Pdk] = {}
        self._revisions: dict[RevisionRef, TechnologyRevision] = {}

    @property
    def pdks(self) -> tuple[Pdk, ...]:
        return tuple(self._pdks.values())

    def add(self, pdk: Pdk) -> list[Diagnostic]:
        """Validate `pdk` (including Waffle compatibility) and register it if it has no errors.

        Adding an identical PDK again does nothing; a different PDK with the same id is
        `pdk.duplicate`.
        """
        found = validate_pdk(pdk, self._waffle_version)
        known = self._pdks.get(pdk.manifest.id)
        if known is not None and known != pdk:
            found.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="pdk.duplicate",
                    message=f"{known.manifest.id} {known.manifest.version} is already loaded "
                    f"and differs from {pdk.manifest.version}",
                    subject=str(pdk.manifest.id),
                )
            )
        if not has_errors(found):
            self._pdks[pdk.manifest.id] = pdk
            for technology in pdk.technologies:
                self._revisions.setdefault(revision_of(technology), technology)
        return found

    def add_directory(self, directory: Path) -> list[Diagnostic]:
        """Read the PDK stored in `directory` and `add` it."""
        result = read_pdk(directory)
        if result.value is None:
            return list(result.diagnostics)
        return [*result.diagnostics, *self.add(result.value)]

    def discover(self, candidates: Iterable[EntryPointLike] | None = None) -> list[Diagnostic]:
        """Load and `add` every PDK entry point. `candidates` defaults to the installed
        entry points of group `waffle.pdks`; tests inject their own."""
        found: list[Diagnostic] = []
        for entry in entry_points(group=ENTRY_POINT_GROUP) if candidates is None else candidates:
            subject = f"{ENTRY_POINT_GROUP}:{entry.name}"
            try:
                provider = entry.load()
                pdk = provider() if callable(provider) else None
            except Exception as exc:  # noqa: BLE001 - third-party plugin code may fail any way
                found.append(
                    _error("pdk.entry_point_failed", f"{type(exc).__name__}: {exc}", subject)
                )
                continue
            if isinstance(pdk, Pdk):
                found.extend(self.add(pdk))
            else:
                found.append(
                    _error("pdk.entry_point_invalid", "entry point must return a Pdk", subject)
                )
        return found

    def revisions(self, technology: EntityId) -> tuple[RevisionRef, ...]:
        """The loaded revisions of `technology`, sorted."""
        return tuple(sorted(ref for ref in self._revisions if ref.entity == technology))

    def resolve(self, ref: RevisionRef) -> Result[TechnologyRevision]:
        """The technology revision pinned by `ref`, or why it is unavailable.

        When the technology is known but not at this digest (for example the PDK was
        updated), the diagnostic lists the available revisions.
        """
        technology = self._revisions.get(ref)
        if technology is not None:
            return Result.of(technology)
        available = self.revisions(ref.entity)
        if not available:
            return Result(
                None,
                (_error("technology.unknown", f"no loaded PDK provides {ref.entity}", str(ref)),),
            )
        listing = ", ".join(str(r) for r in available)
        return Result(
            None,
            (
                _error(
                    "technology.revision_unavailable",
                    f"{ref} is not available; loaded revisions of {ref.entity}: {listing}",
                    str(ref),
                ),
            ),
        )


def _error(code: str, message: str, subject: str) -> Diagnostic:
    return Diagnostic(severity=Severity.ERROR, code=code, message=message, subject=subject)
