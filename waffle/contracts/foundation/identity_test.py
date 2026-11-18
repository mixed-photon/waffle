from __future__ import annotations

import pytest

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId, RevisionRef

DIGEST = "sha256:" + "ab" * 32


def test_entity_id_round_trips_through_text() -> None:
    entity = EntityId("component", "mmi_2x2-v1.0")
    assert str(entity) == "component:mmi_2x2-v1.0"
    assert EntityId.parse(str(entity)) == entity


@pytest.mark.parametrize("kind", ["", "Component", "1abc", "a b", "a:b"])
def test_entity_kind_is_validated(kind: str) -> None:
    with pytest.raises(ValueError, match="kind"):
        EntityId(kind, "x")


@pytest.mark.parametrize("name", ["", "-x", "a b", "a:b", "a@b"])
def test_entity_name_is_validated(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        EntityId("component", name)


def test_entity_parse_requires_separator() -> None:
    with pytest.raises(ValueError, match="kind:name"):
        EntityId.parse("component")


def test_revision_ref_round_trips_through_text() -> None:
    ref = RevisionRef(EntityId("model", "wg"), DIGEST)
    assert str(ref) == f"model:wg@{DIGEST}"
    assert RevisionRef.parse(str(ref)) == ref


def test_revision_ref_of_payload_is_content_addressed() -> None:
    entity = EntityId("model", "wg")
    a = RevisionRef.of(entity, {"width": 0.5, "units": "um"})
    b = RevisionRef.of(entity, {"units": "um", "width": 0.5})
    c = RevisionRef.of(entity, {"width": 0.6, "units": "um"})
    assert a == b
    assert a != c
    assert a.digest == content_hash({"width": 0.5, "units": "um"})


@pytest.mark.parametrize("digest", ["", "sha256:abc", "md5:" + "0" * 32, "sha256:" + "G" * 64])
def test_revision_digest_is_validated(digest: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        RevisionRef(EntityId("model", "wg"), digest)


def test_revision_parse_requires_digest() -> None:
    with pytest.raises(ValueError, match="sha256"):
        RevisionRef.parse("model:wg")


def test_ids_are_hashable_and_ordered() -> None:
    a, b = EntityId("component", "a"), EntityId("component", "b")
    assert {a, b, EntityId("component", "a")} == {a, b}
    assert sorted([b, a]) == [a, b]
