"""Validate a PDK: manifest against contents, each technology, and Waffle compatibility.

Codes: `pdk.incompatible_waffle`, `pdk.duplicate_technology`, `pdk.manifest_mismatch`
(a pinned revision is not shipped, or a shipped revision is not pinned), and every
`validate_technology` code located under `("technologies", i)`.
"""

from __future__ import annotations

from collections import Counter

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.technology.pdk import Pdk
from waffle.contracts.technology.versions import satisfies
from waffle.technology.validate import validate_technology


def validate_pdk(pdk: Pdk, waffle_version: str | None = None) -> list[Diagnostic]:
    """Problems in `pdk`. When `waffle_version` is given, its compatibility is checked too."""
    manifest = pdk.manifest
    subject = str(manifest.id)
    found: list[Diagnostic] = []

    def error(code: str, message: str, *location: str | int) -> None:
        found.append(
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                message=message,
                subject=subject,
                location=location,
            )
        )

    if waffle_version is not None and not satisfies(waffle_version, manifest.waffle):
        error(
            "pdk.incompatible_waffle",
            f"{subject} {manifest.version} needs Waffle {manifest.waffle}, found {waffle_version}",
            "waffle",
        )
    shipped = [revision_of(t) for t in pdk.technologies]
    for ref, count in Counter(shipped).items():
        if count > 1:
            error("pdk.duplicate_technology", f"{ref} is provided {count} times", "technologies")
    pinned = set(manifest.technologies)
    for ref in sorted(pinned - set(shipped)):
        error("pdk.manifest_mismatch", f"manifest pins {ref}, which the PDK does not ship")
    for ref in sorted(set(shipped) - pinned):
        error("pdk.manifest_mismatch", f"PDK ships {ref}, which the manifest does not pin")
    for i, technology in enumerate(pdk.technologies):
        found.extend(
            d.model_copy(update={"location": ("technologies", i, *d.location)})
            for d in validate_technology(technology)
        )
    return found
