from __future__ import annotations

from typing import Any, Literal

import pytest

from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import (
    Record,
    load_record,
    migration,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.foundation.result import Result


class Widget(Record):
    kind: Literal["widget"] = "widget"
    schema_version: Literal[2] = 2
    width_um: float


@migration("widget", 1)
def _widget_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k != "width_nm"}
    out["width_um"] = data["width_nm"] / 1000.0
    return out


def widget(width: float = 0.5, **kwargs: Any) -> Widget:
    return Widget(id=EntityId("widget", "w"), width_um=width, **kwargs)


def codes(result: Result[Any]) -> list[str]:
    return [d.code for d in result.diagnostics]


def test_revision_is_content_addressed_and_covers_metadata() -> None:
    assert revision_of(widget()) == revision_of(widget())
    assert revision_of(widget()).entity == EntityId("widget", "w")
    assert revision_of(widget()) != revision_of(widget(0.6))
    assert revision_of(widget()) != revision_of(widget(metadata={"note": "x"}))


def test_json_round_trip_preserves_revision() -> None:
    original = widget(metadata={"note": "x"})
    text = to_canonical_json(original)
    assert '"id":"widget:w"' in text
    loaded = load_record(text, Widget).unwrap()
    assert loaded == original
    assert revision_of(loaded) == revision_of(original)


def test_published_records_are_immutable() -> None:
    with pytest.raises(ValueError):
        widget().width_um = 1.0  # type: ignore[misc]


def test_id_must_match_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        Widget(id=EntityId("gadget", "w"), width_um=1.0)


def test_older_payload_is_migrated() -> None:
    old = {"kind": "widget", "schema_version": 1, "id": "widget:w", "width_nm": 500.0}
    migrated = load_record(old, Widget).unwrap()
    assert migrated == widget(0.5)
    assert revision_of(migrated) == revision_of(widget(0.5))


def test_newer_payload_is_rejected() -> None:
    result = load_record({"kind": "widget", "schema_version": 3, "id": "widget:w"}, Widget)
    assert not result.ok
    assert codes(result) == ["record.unsupported_version"]


def test_older_payload_without_migration_is_rejected() -> None:
    result = load_record({"kind": "widget", "schema_version": 0, "id": "widget:w"}, Widget)
    assert codes(result) == ["record.no_migration_path"]


def test_duplicate_migration_is_a_programmer_error() -> None:
    with pytest.raises(ValueError, match="already registered"):
        migration("widget", 1)(lambda d: d)


def test_malformed_payloads_give_diagnostics() -> None:
    assert codes(load_record("{nope", Widget)) == ["record.invalid_json"]
    assert codes(load_record("[1]", Widget)) == ["record.invalid"]
    assert codes(load_record({"kind": "gadget", "schema_version": 2}, Widget)) == [
        "record.kind_mismatch"
    ]
    bad = {"kind": "widget", "schema_version": 2, "id": "widget:w", "width_um": "wide", "x": 1}
    result = load_record(bad, Widget)
    assert codes(result) == ["record.invalid", "record.invalid"]
    assert {d.location for d in result.diagnostics} == {("width_um",), ("x",)}


def test_result_unwrap_and_warnings() -> None:
    assert Result.of(3).unwrap() == 3
    with pytest.raises(ValueError):
        Result(None).unwrap()
