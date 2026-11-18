from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.results.artifact import (
    ArtifactManifest,
    ArtifactRef,
    FileEntry,
    parse_reference,
)
from waffle.contracts.results.attempt import AttemptRecord, AttemptStart, AttemptState
from waffle.contracts.results.provenance import InputLink, OperationInfo, input_fingerprint

DIGEST = "sha256:" + "ab" * 32


def _file(name: str = "a.bin") -> FileEntry:
    return FileEntry(name=name, size=3, digest=DIGEST, media_type="application/octet-stream")


def test_artifact_ref_round_trips_and_is_distinct_from_revision_ref() -> None:
    ref = ArtifactRef("dataset", DIGEST)
    assert str(ref) == f"dataset@{DIGEST}"
    assert parse_reference(str(ref)) == ref
    revision = RevisionRef(EntityId("component", "mmi"), DIGEST)
    assert parse_reference(str(revision)) == revision


@pytest.mark.parametrize("text", ["dataset", "Bad@" + DIGEST, "dataset@sha256:xyz"])
def test_invalid_artifact_refs_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        ArtifactRef.parse(text)


def test_manifest_ref_is_content_hash_and_ignores_construction_order() -> None:
    a = ArtifactManifest(kind="dataset", metadata={"x": 1, "y": 2}, files=(_file(),))
    b = ArtifactManifest(kind="dataset", metadata={"y": 2, "x": 1}, files=(_file(),))
    assert a.ref == b.ref
    assert a.ref.digest == content_hash(a.model_dump(mode="json"))
    assert ArtifactManifest.model_validate_json(a.canonical()).ref == a.ref
    other = ArtifactManifest(kind="dataset", metadata={"x": 2, "y": 2}, files=(_file(),))
    assert other.ref != a.ref


def test_manifest_rejects_unsorted_duplicate_and_bad_files() -> None:
    with pytest.raises(ValidationError):
        ArtifactManifest(kind="dataset", files=(_file("b"), _file("a")))
    with pytest.raises(ValidationError):
        ArtifactManifest(kind="dataset", files=(_file("a"), _file("a")))
    with pytest.raises(ValidationError):
        _file("../escape")
    with pytest.raises(ValidationError):
        FileEntry(name="a.npy", size=1, digest=DIGEST, media_type="x", dtype="float64")
    with pytest.raises(ValidationError):
        ArtifactManifest(kind="dataset", metadata={"bad": float("nan")})


def test_input_links_serialize_references_as_strings() -> None:
    link = InputLink(role="fitting_data", target=ArtifactRef("dataset", DIGEST))
    assert link.model_dump(mode="json") == {"role": "fitting_data", "target": f"dataset@{DIGEST}"}
    assert InputLink.model_validate(link.model_dump(mode="json")) == link


def test_fingerprint_depends_on_inputs_not_environment_or_order() -> None:
    inputs = (
        InputLink(target=ArtifactRef("dataset", DIGEST)),
        InputLink(role="reference", target=ArtifactRef("model", DIGEST)),
    )
    op = OperationInfo(name="fit", version="1", parameters_digest=content_hash({}))
    moved = op.model_copy(update={"environment": {"platform": "elsewhere"}})
    assert input_fingerprint(op, inputs) == input_fingerprint(moved, inputs[::-1])
    assert input_fingerprint(op, inputs) != input_fingerprint(op, inputs[:1])


def test_attempt_without_outcome_is_started() -> None:
    op = OperationInfo(name="fit", version="1", parameters_digest=content_hash({}))
    start = AttemptStart(
        attempt_id="20260101T000000Z-0123456789ab",
        operation=op,
        input_fingerprint=input_fingerprint(op, ()),
        started_at=datetime.now(UTC),
    )
    assert AttemptRecord(start=start).state is AttemptState.STARTED
