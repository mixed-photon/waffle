"""Lineage queries over the provenance records of an `ArtifactStore`."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from waffle.artifacts.store import ArtifactStore
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.identity import RevisionRef
from waffle.contracts.results.artifact import ArtifactRef, Reference
from waffle.contracts.results.provenance import InputLink, ProvenanceRecord


@dataclass(frozen=True)
class Lineage:
    """Everything upstream of `root`: the production records walked and any gaps found.

    `records` hold each upstream artifact's operations and inputs (an artifact produced
    by several attempts contributes several records). `diagnostics` report upstream
    artifacts that are not in the store or have no recorded producer.
    """

    root: ArtifactRef
    records: tuple[ProvenanceRecord, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def complete(self) -> bool:
        return not has_errors(self.diagnostics)

    @property
    def artifacts(self) -> tuple[ArtifactRef, ...]:
        """Upstream artifacts, excluding `root`."""
        found = {t for r in self.records for t in _targets(r) if isinstance(t, ArtifactRef)}
        return tuple(sorted(found - {self.root}))

    @property
    def revisions(self) -> tuple[RevisionRef, ...]:
        """Upstream record revisions (leaves: their own provenance is not tracked here)."""
        return tuple(
            sorted({t for r in self.records for t in _targets(r) if isinstance(t, RevisionRef)})
        )


def _targets(record: ProvenanceRecord) -> tuple[Reference, ...]:
    return tuple(link.target for link in record.inputs)


def direct_inputs(store: ArtifactStore, ref: ArtifactRef) -> tuple[InputLink, ...]:
    """The distinct input links of every attempt that produced `ref`."""
    links: dict[InputLink, None] = {}
    for record in store.provenance_of(ref):
        links.update(dict.fromkeys(record.inputs))
    return tuple(links)


def upstream(store: ArtifactStore, ref: ArtifactRef) -> Lineage:
    """Transitive inputs of `ref`. Each artifact is visited once, so cycles terminate."""
    records: list[ProvenanceRecord] = []
    diagnostics: list[Diagnostic] = []
    needed_by: dict[ArtifactRef, str] = {ref: "the requested artifact"}
    queue = deque([ref])
    while queue:
        node = queue.popleft()
        if not store.exists(node):
            diagnostics.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="lineage.missing_artifact",
                    message=f"not in the store (needed by {needed_by[node]})",
                    subject=str(node),
                )
            )
            continue
        found = store.provenance_of(node)
        if not found:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="lineage.no_provenance",
                    message="no attempt is recorded as producing it",
                    subject=str(node),
                )
            )
        records.extend(found)
        for record in found:
            for target in _targets(record):
                if isinstance(target, ArtifactRef) and target not in needed_by:
                    needed_by[target] = str(node)
                    queue.append(target)
    return Lineage(ref, tuple(records), tuple(diagnostics))


def dependents(
    store: ArtifactStore, ref: Reference, *, transitive: bool = False
) -> tuple[ArtifactRef, ...]:
    """Artifacts recorded as produced from `ref` (artifact or record revision).

    Direct dependents only, or every depth when `transitive`.
    """
    users: dict[Reference, set[ArtifactRef]] = {}
    for record in store.all_provenance():
        for target in _targets(record):
            users.setdefault(target, set()).add(record.artifact)
    found: set[ArtifactRef] = set()
    queue: deque[Reference] = deque([ref])
    while queue:
        for user in users.get(queue.popleft(), ()):
            if user not in found:
                found.add(user)
                if transitive:
                    queue.append(user)
    return tuple(sorted(user for user in found if user != ref))
