from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from waffle.artifacts import local
from waffle.artifacts.environment import capture_environment
from waffle.artifacts.local import LocalArtifactStore
from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Severity, has_errors
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.attempt import AttemptState
from waffle.contracts.results.provenance import OperationInfo

OP = OperationInfo(
    name="demo.produce",
    version="1",
    parameters_digest=content_hash({"n": 1}),
    environment=capture_environment(["numpy"]),
)
SAMPLES = np.arange(12, dtype=np.float64).reshape(3, 4)


@pytest.fixture
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "store")


def publish(store: ArtifactStore, *, note: str = "x") -> ArtifactRef:
    with store.attempt(OP) as session:
        writer = session.new_artifact("dataset", {"note": note})
        writer.write_array("samples.npy", SAMPLES)
        writer.write_json("config.json", {"a": 1})
        return writer.commit()


def artifact_dir(store: LocalArtifactStore, ref: ArtifactRef) -> Path:
    return store._artifact_dir(ref)


class Crash(BaseException):
    """Stands in for a process dying: nothing in the store code may catch it."""


# Basic round trip


def test_publish_and_read_back(store: LocalArtifactStore) -> None:
    ref = publish(store)
    manifest = store.inspect(ref)
    assert manifest.ref == ref and manifest.metadata == {"note": "x"}
    assert [f.name for f in manifest.files] == ["config.json", "samples.npy"]
    array_entry = manifest.file("samples.npy")
    assert array_entry is not None
    assert (array_entry.dtype, array_entry.shape) == ("float64", (3, 4))
    np.testing.assert_array_equal(store.load_array(ref, "samples.npy"), SAMPLES)
    assert store.read_bytes(ref, "config.json") == b'{"a":1}'
    assert store.verify(ref) == ()
    assert store.list_artifacts() == (ref,) == store.list_artifacts("dataset")
    assert store.list_artifacts("model") == ()


def test_identical_content_has_identical_reference_across_stores(tmp_path: Path) -> None:
    refs = {publish(LocalArtifactStore(tmp_path / name)) for name in ("a", "b")}
    assert len(refs) == 1


def test_array_layout_does_not_change_identity(store: LocalArtifactStore) -> None:
    refs = []
    for array in (SAMPLES, np.asfortranarray(SAMPLES)):
        with store.attempt(OP) as session:
            writer = session.new_artifact("dataset")
            writer.write_array("x.npy", array)
            refs.append(writer.commit())
    assert refs[0] == refs[1]


def test_writer_rejects_bad_input(store: LocalArtifactStore) -> None:
    with store.attempt(OP) as session:
        writer = session.new_artifact("dataset")
        with pytest.raises(ValueError):
            writer.write_bytes("../escape", b"x")
        with pytest.raises(ValueError):
            writer.write_array("o.npy", np.array(["a"], dtype=object))
        writer.write_bytes("a", b"x")
        with pytest.raises(ValueError):
            writer.write_bytes("a", b"y")
        with pytest.raises(ValueError):
            session.new_artifact("Bad Kind")
        with pytest.raises(ValueError):
            session.new_artifact("dataset", {"nan": float("nan")})
        writer.commit()
        with pytest.raises(RuntimeError):
            writer.write_bytes("b", b"x")


# Criterion 1: failed publication cannot expose incomplete output as complete


def test_exception_mid_write_exposes_nothing(store: LocalArtifactStore) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with store.attempt(OP) as session:
            writer = session.new_artifact("dataset")
            writer.write_bytes("part.bin", b"half")
            raise RuntimeError("boom")
    assert store.list_artifacts() == ()
    [attempt] = store.list_attempts()
    assert attempt.state is AttemptState.FAILED
    assert attempt.outcome is not None and attempt.outcome.produced == ()
    assert attempt.outcome.diagnostics[0].code == "attempt.failed"
    assert "boom" in attempt.outcome.diagnostics[0].message
    [partial] = attempt.partial_outputs
    staged = store._attempt_dir(attempt.start.attempt_id) / "staging" / partial
    assert (staged / "files" / "part.bin").read_bytes() == b"half"
    assert not (staged / "manifest.json").exists()


def test_failure_inside_array_write_exposes_nothing(
    store: LocalArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def half_save(handle: Any, array: Any, **_: Any) -> None:
        handle.write(b"\x93NUMPY partial")
        raise OSError("disk full")

    monkeypatch.setattr(local.np, "save", half_save)
    with pytest.raises(OSError):
        with store.attempt(OP) as session:
            session.new_artifact("dataset").write_array("x.npy", SAMPLES)
    assert store.list_artifacts() == ()
    assert store.list_attempts()[0].state is AttemptState.FAILED


def test_crash_before_publication_leaves_only_staging(
    store: LocalArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(source: Path, destination: Path) -> None:
        raise Crash

    monkeypatch.setattr(local, "_publish_directory", crash)
    with pytest.raises(Crash):
        with store.attempt(OP) as session:
            writer = session.new_artifact("dataset")
            writer.write_bytes("a.bin", b"data")
            writer.commit()
    assert store.list_artifacts() == ()
    [attempt] = store.list_attempts()
    assert attempt.state is AttemptState.CANCELLED
    assert attempt.partial_outputs  # fully staged output stays inspectable
    assert not any(store._root.glob("artifacts/*/*"))
    assert not any(store._root.glob("provenance/*/*"))


def test_killed_process_leaves_started_attempt_and_no_artifact(tmp_path: Path) -> None:
    root = tmp_path / "store"
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from waffle.artifacts.local import LocalArtifactStore
        from waffle.contracts.results.provenance import OperationInfo
        store = LocalArtifactStore(Path({str(root)!r}))
        op = OperationInfo(name="demo.produce", version="1", parameters_digest="sha256:" + "0" * 64)
        session = store.attempt(op)
        writer = session.new_artifact("dataset")
        writer.write_bytes("a.bin", b"data")
        os._exit(9)
        """
    )
    assert _run(script).returncode == 9
    store = LocalArtifactStore(root)
    [attempt] = store.list_attempts()
    assert attempt.state is AttemptState.STARTED and attempt.outcome is None
    assert len(attempt.partial_outputs) == 1
    assert store.list_artifacts() == ()


def test_writers_cannot_be_opened_on_ended_attempts(store: LocalArtifactStore) -> None:
    publish(store)
    [attempt] = store.list_attempts()
    with pytest.raises(ArtifactError) as info:
        store.open_writer(attempt.start.attempt_id, "dataset")
    assert info.value.diagnostics[0].code == "attempt.already_finished"


# Criterion 2: retrying publication is idempotent


def test_republishing_same_content_returns_same_reference(store: LocalArtifactStore) -> None:
    first, second = publish(store), publish(store)
    assert first == second
    assert store.list_artifacts() == (first,)
    assert store.verify(first) == ()
    assert len(store.list_attempts()) == 2
    assert len(store.provenance_of(first)) == 2  # one record per producing attempt


def test_committing_one_writer_twice_is_a_no_op(store: LocalArtifactStore) -> None:
    with store.attempt(OP) as session:
        writer = session.new_artifact("dataset")
        writer.write_bytes("a.bin", b"data")
        assert writer.commit() == writer.commit() == writer.published
    assert len(store.provenance_of(writer.published)) == 1  # type: ignore[arg-type]


def test_retry_after_crash_between_publish_and_provenance_heals(
    store: LocalArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = local._write_new

    def fail_on_provenance(path: Path, data: bytes) -> bool:
        if path.parent.parent.name == "provenance":
            raise Crash
        return real(path, data)

    session = store.attempt(OP)
    writer = session.new_artifact("dataset")
    writer.write_bytes("a.bin", b"data")
    with monkeypatch.context() as patch:
        patch.setattr(local, "_write_new", fail_on_provenance)
        with pytest.raises(Crash):
            writer.commit()
    [ref] = store.list_artifacts()
    assert [d.code for d in store.verify(ref)] == ["artifact.no_provenance"]
    assert store.verify(ref)[0].severity is Severity.WARNING
    assert writer.commit() == ref  # same writer: publication is idempotent and records provenance
    assert store.verify(ref) == ()


def test_retry_does_not_overwrite_completed_result(store: LocalArtifactStore) -> None:
    first = publish(store, note="x")
    manifest_before = artifact_dir(store, first).joinpath("manifest.json").read_bytes()
    second = publish(store, note="changed")
    assert first != second
    assert store.list_artifacts() == tuple(sorted((first, second)))
    assert artifact_dir(store, first).joinpath("manifest.json").read_bytes() == manifest_before
    attempts = store.list_attempts(operation="demo.produce")
    assert [a.state for a in attempts] == [AttemptState.SUCCEEDED] * 2
    assert attempts[0].start.input_fingerprint == attempts[1].start.input_fingerprint
    assert store.list_attempts(fingerprint=attempts[0].start.input_fingerprint) == attempts
    assert store.list_attempts(operation="other") == ()


def test_racing_threads_publish_one_artifact(store: LocalArtifactStore) -> None:
    barrier = threading.Barrier(8)
    refs: list[ArtifactRef] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with store.attempt(OP) as session:
                writer = session.new_artifact("dataset")
                writer.write_array("samples.npy", SAMPLES)
                barrier.wait()
                refs.append(writer.commit())
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(refs)) == 1
    assert store.list_artifacts() == (refs[0],)
    assert store.verify(refs[0]) == ()
    assert len(store.provenance_of(refs[0])) == 8
    assert all(a.partial_outputs == () for a in store.list_attempts())


def test_racing_processes_publish_one_artifact(tmp_path: Path) -> None:
    root = tmp_path / "store"
    script = textwrap.dedent(
        f"""
        import numpy as np
        from pathlib import Path
        from waffle.artifacts.local import LocalArtifactStore
        from waffle.contracts.results.provenance import OperationInfo
        store = LocalArtifactStore(Path({str(root)!r}))
        op = OperationInfo(name="demo.produce", version="1", parameters_digest="sha256:" + "0" * 64)
        for _ in range(20):
            with store.attempt(op) as session:
                writer = session.new_artifact("dataset")
                writer.write_array("x.npy", np.arange(1000.0))
                print(writer.commit())
        """
    )
    procs = [_start(script) for _ in range(4)]
    outputs = [p.communicate() for p in procs]
    assert all(p.returncode == 0 for p in procs), outputs
    printed = {line for out, _ in outputs for line in out.split()}
    store = LocalArtifactStore(root)
    [ref] = store.list_artifacts()
    assert printed == {str(ref)}
    assert store.verify(ref) == ()
    assert len(store.provenance_of(ref)) == 80
    assert all(a.state is AttemptState.SUCCEEDED for a in store.list_attempts())


# Attempts


def test_attempt_states_and_single_outcome(store: LocalArtifactStore) -> None:
    with pytest.raises(KeyboardInterrupt):
        with store.attempt(OP):
            raise KeyboardInterrupt
    with store.attempt(OP) as ok:
        pass
    [cancelled, succeeded] = store.list_attempts()
    assert cancelled.state is AttemptState.CANCELLED
    assert cancelled.outcome is not None
    assert cancelled.outcome.diagnostics[0].code == "attempt.cancelled"
    assert succeeded.state is AttemptState.SUCCEEDED and succeeded.outcome is not None
    assert succeeded.start.attempt_id == ok.attempt_id
    assert succeeded.start.operation == OP
    with pytest.raises(ArtifactError) as info:
        store.finish_attempt(ok.attempt_id, AttemptState.FAILED)
    assert info.value.diagnostics[0].code == "attempt.already_finished"
    assert store.get_attempt(ok.attempt_id).state is AttemptState.SUCCEEDED
    with pytest.raises(ArtifactError):
        store.get_attempt("20200101T000000Z-000000000000")


def test_failed_attempt_keeps_artifacts_it_published_before_failing(
    store: LocalArtifactStore,
) -> None:
    with pytest.raises(RuntimeError):
        with store.attempt(OP) as session:
            writer = session.new_artifact("dataset")
            writer.write_bytes("a.bin", b"data")
            ref = writer.commit()
            raise RuntimeError
    [attempt] = store.list_attempts()
    assert attempt.state is AttemptState.FAILED
    assert attempt.outcome is not None and attempt.outcome.produced == (ref,)
    assert store.verify(ref) == ()


def test_artifact_error_diagnostics_are_recorded_on_the_attempt(store: LocalArtifactStore) -> None:
    with pytest.raises(ArtifactError):
        with store.attempt(OP):
            store.inspect(ArtifactRef("dataset", "sha256:" + "0" * 64))
    [attempt] = store.list_attempts()
    assert attempt.outcome is not None
    assert [d.code for d in attempt.outcome.diagnostics] == ["artifact.not_found"]


# Criterion 3: metadata inspection avoids loading large arrays


def test_inspect_never_touches_payload_files(store: LocalArtifactStore) -> None:
    ref = publish(store)
    shutil_payload = artifact_dir(store, ref) / "files"
    for path in shutil_payload.iterdir():
        path.unlink()  # any payload read would now fail
    shutil_payload.rmdir()
    manifest = store.inspect(ref)
    entry = manifest.file("samples.npy")
    assert entry is not None and entry.shape == (3, 4) and entry.dtype == "float64"
    assert store.exists(ref) and store.list_artifacts() == (ref,)
    with pytest.raises(ArtifactError):
        store.load_array(ref, "samples.npy")


def test_inspect_reads_only_the_manifest(
    store: LocalArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = publish(store)
    opened: list[str] = []
    real_open = Path.open

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    store.inspect(ref)
    assert opened == ["manifest.json"]


def test_mmap_loads_lazily_and_read_only(store: LocalArtifactStore) -> None:
    ref = publish(store)
    array = store.load_array(ref, "samples.npy", mmap=True)
    assert isinstance(array, np.memmap) and not array.flags.writeable
    np.testing.assert_array_equal(array[1], SAMPLES[1])
    with pytest.raises(ArtifactError) as info:
        store.load_array(ref, "config.json")
    assert info.value.diagnostics[0].code == "artifact.not_an_array"
    with pytest.raises(ArtifactError) as info:
        store.load_array(ref, "nothing.npy")
    assert info.value.diagnostics[0].code == "artifact.file_unknown"


# Criterion 4: corruption is detected


def codes(store: ArtifactStore, ref: ArtifactRef) -> list[str]:
    return [d.code for d in store.verify(ref)]


def test_changed_bytes_are_detected(store: LocalArtifactStore) -> None:
    ref = publish(store)
    payload = artifact_dir(store, ref) / "files" / "config.json"
    payload.write_bytes(b'{"a":2}')  # same size, different content
    assert codes(store, ref) == ["artifact.file_corrupt"]
    with pytest.raises(ArtifactError) as info:
        store.read_bytes(ref, "config.json")
    assert info.value.diagnostics[0].code == "artifact.file_corrupt"


def test_corrupt_array_is_detected_on_load_and_verify(store: LocalArtifactStore) -> None:
    ref = publish(store)
    payload = artifact_dir(store, ref) / "files" / "samples.npy"
    data = bytearray(payload.read_bytes())
    data[-1] ^= 0xFF
    payload.write_bytes(bytes(data))
    assert codes(store, ref) == ["artifact.file_corrupt"]
    with pytest.raises(ArtifactError):
        store.load_array(ref, "samples.npy")
    payload.write_bytes(data[:-8])  # truncated: the cheap mmap check catches the size change
    with pytest.raises(ArtifactError):
        store.load_array(ref, "samples.npy", mmap=True)


def test_missing_file_is_detected(store: LocalArtifactStore) -> None:
    ref = publish(store)
    (artifact_dir(store, ref) / "files" / "config.json").unlink()
    assert codes(store, ref) == ["artifact.file_missing"]
    with pytest.raises(ArtifactError) as info:
        store.read_bytes(ref, "config.json")
    assert info.value.diagnostics[0].code == "artifact.file_missing"


def test_extra_file_is_detected(store: LocalArtifactStore) -> None:
    ref = publish(store)
    (artifact_dir(store, ref) / "files" / "stowaway.bin").write_bytes(b"x")
    (artifact_dir(store, ref) / "notes.txt").write_bytes(b"x")
    assert codes(store, ref) == ["artifact.file_unexpected"] * 2


def test_tampered_manifest_is_detected(store: LocalArtifactStore) -> None:
    ref = publish(store)
    manifest = artifact_dir(store, ref) / "manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b'"x"', b'"y"'))
    assert codes(store, ref) == ["artifact.manifest_corrupt"]
    with pytest.raises(ArtifactError):
        store.inspect(ref)
    with pytest.raises(ArtifactError):
        store.read_bytes(ref, "config.json")
    manifest.write_bytes(b"{not json")
    assert codes(store, ref) == ["artifact.manifest_corrupt"]


def test_missing_artifact_verify_reports_error(store: LocalArtifactStore) -> None:
    diagnostics = store.verify(ArtifactRef("dataset", "sha256:" + "1" * 64))
    assert has_errors(diagnostics) and diagnostics[0].code == "artifact.not_found"


# Helpers


def _start(script: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run(script: str) -> subprocess.CompletedProcess[str]:
    process = _start(script)
    out, err = process.communicate()
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)
