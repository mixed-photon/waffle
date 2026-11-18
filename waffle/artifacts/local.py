"""`LocalArtifactStore`: artifacts, attempts, and provenance as plain files under one directory.

Layout (see docs/artifacts.md):

    artifacts/<kind>/<hex>/manifest.json, files/<name>   immutable once visible
    attempts/<attempt_id>/attempt.json, outcome.json      each written once
    attempts/<attempt_id>/staging/<kind>-<id>/...         unpublished output
    provenance/<hex>/<attempt_id>.json                    each written once

Publication renames a fully written staging directory onto its final path. That
needs the whole store on one local POSIX filesystem, where rename is atomic.
"""

from __future__ import annotations

import errno
import hashlib
import io
import os
import shutil
import uuid
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ValidationError

from waffle.artifacts.store import ArtifactError, ArtifactStore, ArtifactWriter
from waffle.contracts.foundation.canonical import HASH_ALGORITHM
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.results.artifact import FILE_NAME, ArtifactManifest, ArtifactRef, FileEntry
from waffle.contracts.results.attempt import (
    AttemptOutcome,
    AttemptRecord,
    AttemptStart,
    AttemptState,
)
from waffle.contracts.results.provenance import (
    ATTEMPT_ID,
    InputLink,
    OperationInfo,
    ProvenanceRecord,
    input_fingerprint,
)

MANIFEST = "manifest.json"
FILES = "files"
NPY_MEDIA_TYPE = "application/x-npy"
_CHUNK = 1 << 20


def _error(
    code: str, message: str, subject: object, severity: Severity = Severity.ERROR
) -> Diagnostic:
    return Diagnostic(severity=severity, code=code, message=message, subject=str(subject))


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_new(path: Path, data: bytes) -> bool:
    """Create `path` with complete content, or return False if it exists.

    The content is written and synced under a temporary name, then hard-linked into
    place, so a reader never sees a partial file and a second writer cannot replace it.
    """
    temp = path.with_name(f".tmp-{uuid.uuid4().hex}")
    _write_file(temp, data)
    try:
        os.link(temp, path)
    except FileExistsError:
        return False
    finally:
        temp.unlink()
    _fsync_dir(path.parent)
    return True


def _hash_file(path: Path) -> tuple[int, str]:
    sha, size = hashlib.new(HASH_ALGORITHM), 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha.update(chunk)
            size += len(chunk)
    return size, f"{HASH_ALGORITHM}:{sha.hexdigest()}"


def _publish_directory(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _read_model[M: BaseModel](model: type[M], path: Path, code: str, subject: object) -> M:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ArtifactError(_error(code, f"cannot read {path.name}: {exc}", subject)) from exc


def _json(model: BaseModel) -> bytes:
    return model.model_dump_json(indent=2).encode("utf-8")


class LocalArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self._root = root
        for sub in ("artifacts", "attempts", "provenance"):
            (root / sub).mkdir(parents=True, exist_ok=True)

    # Paths

    def _artifact_dir(self, ref: ArtifactRef) -> Path:
        return self._root / "artifacts" / ref.kind / ref.digest.partition(":")[2]

    def _attempt_dir(self, attempt_id: str) -> Path:
        if not ATTEMPT_ID.fullmatch(attempt_id):
            raise ValueError(f"invalid attempt id {attempt_id!r}")
        return self._root / "attempts" / attempt_id

    def _provenance_dir(self, ref: ArtifactRef) -> Path:
        return self._root / "provenance" / ref.digest.partition(":")[2]

    # Attempts

    def start_attempt(
        self, operation: OperationInfo, inputs: Iterable[InputLink] = ()
    ) -> AttemptRecord:
        links = tuple(inputs)
        now = datetime.now(UTC)
        start = AttemptStart(
            attempt_id=f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}",
            operation=operation,
            inputs=links,
            input_fingerprint=input_fingerprint(operation, links),
            started_at=now,
        )
        directory = self._attempt_dir(start.attempt_id)
        (directory / "staging").mkdir(parents=True)
        _write_new(directory / "attempt.json", _json(start))
        return AttemptRecord(start=start)

    def finish_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        diagnostics: Iterable[Diagnostic] = (),
        produced: Iterable[ArtifactRef] = (),
    ) -> AttemptRecord:
        directory = self._attempt_dir(attempt_id)
        self.get_attempt(attempt_id)
        outcome = AttemptOutcome.model_validate(
            {
                "state": state,
                "finished_at": datetime.now(UTC),
                "diagnostics": tuple(diagnostics),
                "produced": tuple(produced),
            }
        )
        if not _write_new(directory / "outcome.json", _json(outcome)):
            raise ArtifactError(
                _error("attempt.already_finished", "attempt has already ended", attempt_id)
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> AttemptRecord:
        directory = self._attempt_dir(attempt_id)
        if not (directory / "attempt.json").is_file():
            raise ArtifactError(_error("attempt.not_found", "no such attempt", attempt_id))
        start = _read_model(AttemptStart, directory / "attempt.json", "attempt.corrupt", attempt_id)
        outcome_path = directory / "outcome.json"
        outcome = (
            _read_model(AttemptOutcome, outcome_path, "attempt.corrupt", attempt_id)
            if outcome_path.is_file()
            else None
        )
        staging = directory / "staging"
        partial = tuple(sorted(p.name for p in staging.iterdir())) if staging.is_dir() else ()
        return AttemptRecord(start=start, outcome=outcome, partial_outputs=partial)

    def list_attempts(
        self, *, operation: str | None = None, fingerprint: str | None = None
    ) -> tuple[AttemptRecord, ...]:
        records = [
            self.get_attempt(p.name)
            for p in (self._root / "attempts").iterdir()
            if ATTEMPT_ID.fullmatch(p.name) and (p / "attempt.json").is_file()
        ]
        return tuple(
            sorted(
                (
                    r
                    for r in records
                    if (operation is None or r.start.operation.name == operation)
                    and (fingerprint is None or r.start.input_fingerprint == fingerprint)
                ),
                key=lambda r: (r.start.started_at, r.start.attempt_id),
            )
        )

    def open_writer(
        self, attempt_id: str, kind: str, metadata: Mapping[str, Any] | None = None
    ) -> ArtifactWriter:
        record = self.get_attempt(attempt_id)
        if record.outcome is not None:
            raise ArtifactError(
                _error(
                    "attempt.already_finished", "cannot add output to an ended attempt", attempt_id
                )
            )
        return _LocalWriter(self, record.start, kind, dict(metadata or {}))

    # Artifacts

    def exists(self, ref: ArtifactRef) -> bool:
        return (self._artifact_dir(ref) / MANIFEST).is_file()

    def inspect(self, ref: ArtifactRef) -> ArtifactManifest:
        path = self._artifact_dir(ref) / MANIFEST
        if not path.is_file():
            raise ArtifactError(_error("artifact.not_found", "no such artifact in this store", ref))
        manifest = _read_model(ArtifactManifest, path, "artifact.manifest_corrupt", ref)
        if manifest.ref != ref:
            raise ArtifactError(
                _error(
                    "artifact.manifest_corrupt",
                    f"manifest content hashes to {manifest.ref}, not the stored reference",
                    ref,
                )
            )
        return manifest

    def list_artifacts(self, kind: str | None = None) -> tuple[ArtifactRef, ...]:
        refs: list[ArtifactRef] = []
        for kind_dir in (self._root / "artifacts").iterdir():
            if kind is not None and kind_dir.name != kind:
                continue
            for entry in kind_dir.iterdir():
                try:
                    refs.append(ArtifactRef(kind_dir.name, f"{HASH_ALGORITHM}:{entry.name}"))
                except ValueError:
                    continue
        return tuple(sorted(refs))

    def _entry_path(self, ref: ArtifactRef, name: str) -> tuple[FileEntry, Path]:
        entry = self.inspect(ref).file(name)
        if entry is None:
            raise ArtifactError(_error("artifact.file_unknown", f"no file {name!r}", ref))
        path = self._artifact_dir(ref) / FILES / name
        if not path.is_file():
            raise ArtifactError(_error("artifact.file_missing", f"file {name!r} is missing", ref))
        return entry, path

    def read_bytes(self, ref: ArtifactRef, name: str) -> bytes:
        entry, path = self._entry_path(ref, name)
        data = path.read_bytes()
        if len(data) != entry.size or _digest(data) != entry.digest:
            raise ArtifactError(
                _error("artifact.file_corrupt", f"file {name!r} does not match its digest", ref)
            )
        return data

    def load_array(self, ref: ArtifactRef, name: str, *, mmap: bool = False) -> NDArray[Any]:
        entry, path = self._entry_path(ref, name)
        if entry.dtype is None:
            raise ArtifactError(
                _error("artifact.not_an_array", f"file {name!r} is not an array", ref)
            )
        if not mmap:
            return np.load(io.BytesIO(self.read_bytes(ref, name)), allow_pickle=False)
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
        except ValueError as exc:
            raise ArtifactError(
                _error("artifact.file_corrupt", f"array {name!r} is unreadable: {exc}", ref)
            ) from exc
        if (
            path.stat().st_size != entry.size
            or str(array.dtype) != entry.dtype
            or array.shape != entry.shape
        ):
            raise ArtifactError(
                _error("artifact.file_corrupt", f"array {name!r} does not match its manifest", ref)
            )
        return array

    def verify(self, ref: ArtifactRef) -> tuple[Diagnostic, ...]:
        try:
            manifest = self.inspect(ref)
        except ArtifactError as exc:
            return exc.diagnostics
        directory = self._artifact_dir(ref)
        found: list[Diagnostic] = []
        for entry in manifest.files:
            path = directory / FILES / entry.name
            if not path.is_file():
                found.append(
                    _error("artifact.file_missing", f"file {entry.name!r} is missing", ref)
                )
            elif _hash_file(path) != (entry.size, entry.digest):
                found.append(_error("artifact.file_corrupt", f"file {entry.name!r} changed", ref))
        expected = {f.name for f in manifest.files}
        files_dir = directory / FILES
        extras = (
            [p.name for p in files_dir.iterdir() if p.name not in expected]
            if files_dir.is_dir()
            else []
        )
        extras += [p.name for p in directory.iterdir() if p.name not in {MANIFEST, FILES}]
        found += [
            _error("artifact.file_unexpected", f"unlisted entry {name!r}", ref) for name in extras
        ]
        if not self.provenance_of(ref):
            found.append(
                _error(
                    "artifact.no_provenance",
                    "no attempt recorded as producing it",
                    ref,
                    Severity.WARNING,
                )
            )
        return tuple(found)

    # Provenance

    def provenance_of(self, ref: ArtifactRef) -> tuple[ProvenanceRecord, ...]:
        directory = self._provenance_dir(ref)
        if not directory.is_dir():
            return ()
        records = [self._read_record(p) for p in directory.glob("*.json")]
        return tuple(
            sorted(
                (r for r in records if r.artifact == ref),
                key=lambda r: (r.recorded_at, r.attempt_id),
            )
        )

    def all_provenance(self) -> Iterator[ProvenanceRecord]:
        for path in sorted((self._root / "provenance").glob("*/*.json")):
            yield self._read_record(path)

    @staticmethod
    def _read_record(path: Path) -> ProvenanceRecord:
        return _read_model(ProvenanceRecord, path, "provenance.corrupt", path.parent.name)


def _digest(data: bytes) -> str:
    return f"{HASH_ALGORITHM}:{hashlib.new(HASH_ALGORITHM, data).hexdigest()}"


class _LocalWriter(ArtifactWriter):
    def __init__(
        self, store: LocalArtifactStore, start: AttemptStart, kind: str, metadata: dict[str, Any]
    ) -> None:
        self._store = store
        self._start = start
        self._kind = kind
        self._metadata = metadata
        self._entries: dict[str, FileEntry] = {}
        self._published: ArtifactRef | None = None
        # Validates kind and metadata now rather than at commit.
        ArtifactManifest(kind=kind, metadata=metadata)
        self._dir = (
            store._attempt_dir(start.attempt_id) / "staging" / f"{kind}-{uuid.uuid4().hex[:8]}"
        )
        (self._dir / FILES).mkdir(parents=True)

    @property
    def published(self) -> ArtifactRef | None:
        return self._published

    def _new_path(self, name: str) -> Path:
        if self._published is not None:
            raise RuntimeError("artifact already committed")
        if not FILE_NAME.fullmatch(name):
            raise ValueError(f"invalid file name {name!r}")
        if name in self._entries:
            raise ValueError(f"file {name!r} already written")
        return self._dir / FILES / name

    def write_bytes(
        self, name: str, data: bytes, media_type: str = "application/octet-stream"
    ) -> None:
        _write_file(self._new_path(name), data)
        self._entries[name] = FileEntry(
            name=name, size=len(data), digest=_digest(data), media_type=media_type
        )

    def write_array(self, name: str, array: NDArray[Any]) -> None:
        if array.dtype.kind not in "biufc":
            raise ValueError(f"unsupported array dtype {array.dtype}")
        path = self._new_path(name)
        contiguous = np.ascontiguousarray(array)  # one byte layout per value
        with path.open("wb") as handle:
            np.save(handle, contiguous, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        size, digest = _hash_file(path)
        self._entries[name] = FileEntry(
            name=name,
            size=size,
            digest=digest,
            media_type=NPY_MEDIA_TYPE,
            dtype=str(contiguous.dtype),
            shape=contiguous.shape,
        )

    def commit(self) -> ArtifactRef:
        if self._published is not None:
            return self._published
        manifest = ArtifactManifest(
            kind=self._kind,
            metadata=self._metadata,
            files=tuple(self._entries[n] for n in sorted(self._entries)),
        )
        ref = manifest.ref
        destination = self._store._artifact_dir(ref)
        if self._dir.is_dir():  # not yet moved by an earlier, interrupted commit
            _write_file(self._dir / MANIFEST, manifest.canonical().encode("utf-8"))
            _fsync_dir(self._dir / FILES)
            _fsync_dir(self._dir)
            destination.parent.mkdir(exist_ok=True)
            try:
                _publish_directory(self._dir, destination)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not destination.is_dir():
                    raise
                shutil.rmtree(self._dir)  # identical content was published first
            _fsync_dir(destination.parent)
        record = ProvenanceRecord(
            artifact=ref,
            attempt_id=self._start.attempt_id,
            operation=self._start.operation,
            inputs=self._start.inputs,
            recorded_at=datetime.now(UTC),
        )
        provenance = self._store._provenance_dir(ref)
        provenance.mkdir(exist_ok=True)
        _write_new(provenance / f"{self._start.attempt_id}.json", _json(record))
        self._published = ref
        return ref
