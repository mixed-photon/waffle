"""The artifact store interface and the attempt session built on it.

Signatures use references, names, bytes, and arrays only, never paths, so an
object-store implementation can sit behind the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from types import TracebackType
from typing import Any, Self

from numpy.typing import NDArray

from waffle.contracts.foundation.canonical import canonical_json
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.results.artifact import ArtifactManifest, ArtifactRef
from waffle.contracts.results.attempt import AttemptRecord, AttemptState
from waffle.contracts.results.provenance import InputLink, OperationInfo, ProvenanceRecord


class ArtifactError(Exception):
    """An artifact operation failed for a reason the caller can act on."""

    def __init__(self, *diagnostics: Diagnostic) -> None:
        super().__init__("; ".join(str(d) for d in diagnostics))
        self.diagnostics = diagnostics


class ArtifactWriter(ABC):
    """Stages the files of one artifact inside an attempt; `commit` publishes it atomically.

    Nothing staged is visible as an artifact until `commit` returns. Commit is
    idempotent: publishing content that already exists returns the same reference.
    """

    @abstractmethod
    def write_bytes(
        self, name: str, data: bytes, media_type: str = "application/octet-stream"
    ) -> None: ...

    @abstractmethod
    def write_array(self, name: str, array: NDArray[Any]) -> None:
        """Store a numeric array as `.npy`; dtype and shape go into the manifest."""

    @abstractmethod
    def commit(self) -> ArtifactRef:
        """Verify the staged files and publish the artifact with its provenance."""

    @property
    @abstractmethod
    def published(self) -> ArtifactRef | None:
        """The reference once `commit` has succeeded, else None."""

    def write_json(self, name: str, value: Any) -> None:
        self.write_bytes(name, canonical_json(value).encode("utf-8"), "application/json")


class ArtifactStore(ABC):
    """Immutable artifacts, append-only attempts, and provenance."""

    # Attempts

    @abstractmethod
    def start_attempt(
        self, operation: OperationInfo, inputs: Iterable[InputLink] = ()
    ) -> AttemptRecord: ...

    @abstractmethod
    def finish_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        diagnostics: Iterable[Diagnostic] = (),
        produced: Iterable[ArtifactRef] = (),
    ) -> AttemptRecord:
        """End an attempt. Raises `ArtifactError` if it already ended or is unknown."""

    @abstractmethod
    def get_attempt(self, attempt_id: str) -> AttemptRecord: ...

    @abstractmethod
    def list_attempts(
        self, *, operation: str | None = None, fingerprint: str | None = None
    ) -> tuple[AttemptRecord, ...]:
        """Attempts ordered by start, optionally for one operation name or input fingerprint."""

    @abstractmethod
    def open_writer(
        self, attempt_id: str, kind: str, metadata: Mapping[str, Any] | None = None
    ) -> ArtifactWriter: ...

    # Artifacts

    @abstractmethod
    def exists(self, ref: ArtifactRef) -> bool: ...

    @abstractmethod
    def inspect(self, ref: ArtifactRef) -> ArtifactManifest:
        """The manifest only: no payload file is opened. Raises `ArtifactError` if absent."""

    @abstractmethod
    def list_artifacts(self, kind: str | None = None) -> tuple[ArtifactRef, ...]: ...

    @abstractmethod
    def read_bytes(self, ref: ArtifactRef, name: str) -> bytes:
        """The file's bytes, checked against the manifest digest."""

    @abstractmethod
    def load_array(self, ref: ArtifactRef, name: str, *, mmap: bool = False) -> NDArray[Any]:
        """Load an array. Without `mmap` the file digest is checked; with `mmap` the array
        is mapped lazily and only size, dtype, and shape are checked (use `verify` for digests)."""

    @abstractmethod
    def verify(self, ref: ArtifactRef) -> tuple[Diagnostic, ...]:
        """Recompute every digest; empty when the artifact is intact."""

    # Provenance

    @abstractmethod
    def provenance_of(self, ref: ArtifactRef) -> tuple[ProvenanceRecord, ...]:
        """Every record of an attempt that produced `ref`, oldest first."""

    @abstractmethod
    def all_provenance(self) -> Iterator[ProvenanceRecord]: ...

    def attempt(self, operation: OperationInfo, inputs: Iterable[InputLink] = ()) -> AttemptSession:
        """Run an operation as one attempt: `with store.attempt(op, inputs) as session:`."""
        return AttemptSession(self, self.start_attempt(operation, inputs))


class AttemptSession:
    """Ends its attempt on exit: succeeded, failed (an `Exception`), or cancelled (any other
    `BaseException`, such as `KeyboardInterrupt`). The exception is never swallowed."""

    def __init__(self, store: ArtifactStore, record: AttemptRecord) -> None:
        self._store = store
        self._writers: list[ArtifactWriter] = []
        self.attempt_id = record.start.attempt_id

    def new_artifact(self, kind: str, metadata: Mapping[str, Any] | None = None) -> ArtifactWriter:
        writer = self._store.open_writer(self.attempt_id, kind, metadata)
        self._writers.append(writer)
        return writer

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        produced = [w.published for w in self._writers if w.published is not None]
        if exc is None:
            state, diagnostics = AttemptState.SUCCEEDED, ()
        elif isinstance(exc, ArtifactError):
            state, diagnostics = AttemptState.FAILED, exc.diagnostics
        else:
            failed = isinstance(exc, Exception)
            state = AttemptState.FAILED if failed else AttemptState.CANCELLED
            code = "attempt.failed" if failed else "attempt.cancelled"
            diagnostics = (
                Diagnostic(
                    severity=Severity.ERROR,
                    code=code,
                    message=f"{type(exc).__name__}: {exc}",
                    subject=self.attempt_id,
                ),
            )
        self._store.finish_attempt(self.attempt_id, state, diagnostics, produced)
