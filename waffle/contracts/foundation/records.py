"""Versioned, immutable, content-addressed records.

A `Record` subclass fixes its `kind` and current `schema_version` as `Literal`
defaults. Publishing a record means taking its `RevisionRef`: the hash of the canonical
JSON of the *whole* record (`revision_of`), so any change, including to `metadata`,
yields a different revision. Loading (`load_record`) checks the schema version and
applies registered `vN -> vN+1` migrations before validating.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    ValidationError,
    WithJsonSchema,
    model_validator,
)

from waffle.contracts.foundation.canonical import canonical_json
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.result import Result

_STRING = WithJsonSchema({"type": "string"})


def _entity_id(value: object) -> EntityId:
    if isinstance(value, EntityId):
        return value
    if isinstance(value, str):
        return EntityId.parse(value)
    raise ValueError("entity id must be a 'kind:name' string")


def _revision_ref(value: object) -> RevisionRef:
    if isinstance(value, RevisionRef):
        return value
    if isinstance(value, str):
        return RevisionRef.parse(value)
    raise ValueError("revision ref must be a 'kind:name@sha256:<hex>' string")


# Both serialize as their string form, so JSON payloads stay human-readable.
EntityIdField = Annotated[
    EntityId, PlainValidator(_entity_id), PlainSerializer(str, return_type=str), _STRING
]
RevisionRefField = Annotated[
    RevisionRef, PlainValidator(_revision_ref), PlainSerializer(str, return_type=str), _STRING
]


def ref_to(kind: str) -> AfterValidator:
    """Validator restricting a `RevisionRefField` to entities of `kind`."""

    def check(ref: RevisionRef) -> RevisionRef:
        if ref.entity.kind != kind:
            raise ValueError(f"expected a {kind!r} revision, got {ref}")
        return ref

    return AfterValidator(check)


class Record(BaseModel):
    """Base of all published records. Subclasses set `kind` and `schema_version`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    schema_version: int
    id: EntityIdField
    # Descriptive text only. It is part of the revision hash; operation-specific
    # fingerprints that ignore it are defined where those operations are.
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _id_matches_kind(self) -> Self:
        if self.id.kind != self.kind:
            raise ValueError(f"id {self.id} must have kind {self.kind!r}")
        return self

    @classmethod
    def current_version(cls) -> int:
        version = cls.model_fields["schema_version"].default
        assert isinstance(version, int)
        return version


Migration = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATIONS: dict[tuple[str, int], Migration] = {}

# Resolves a revision to its record, or None if unavailable.
type RecordLookup = Callable[[RevisionRef], Record | None]


def migration(kind: str, from_version: int) -> Callable[[Migration], Migration]:
    """Register the function upgrading `kind` payloads from `from_version` to the next.

    The function returns the upgraded payload; `schema_version` is advanced for it.
    """

    def register(fn: Migration) -> Migration:
        if (kind, from_version) in _MIGRATIONS:
            raise ValueError(f"migration for {kind!r} v{from_version} already registered")
        _MIGRATIONS[(kind, from_version)] = fn
        return fn

    return register


def to_canonical_json(record: Record) -> str:
    return canonical_json(record.model_dump(mode="json"))


def revision_of(record: Record) -> RevisionRef:
    return RevisionRef.of(record.id, record.model_dump(mode="json"))


def load_record[R: Record](payload: str | Mapping[str, Any], expected: type[R]) -> Result[R]:
    """Parse, version-check, migrate, and validate a payload as an `expected` record."""
    kind = expected.model_fields["kind"].default
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            return _fail("record.invalid_json", f"payload is not valid JSON: {exc}")
    if not isinstance(payload, Mapping):
        return _fail("record.invalid", "payload must be a JSON object")
    data = dict(payload)
    version = data.get("schema_version")
    if data.get("kind") != kind or not isinstance(version, int) or isinstance(version, bool):
        return _fail(
            "record.kind_mismatch",
            f"expected kind {kind!r} with an integer schema_version, "
            f"got kind {data.get('kind')!r} and schema_version {version!r}",
        )
    current = expected.current_version()
    if version > current:
        return _fail(
            "record.unsupported_version",
            f"{kind} schema_version {version} is newer than supported version {current}",
        )
    while version < current:
        step = _MIGRATIONS.get((kind, version))
        if step is None:
            return _fail(
                "record.no_migration_path",
                f"no migration for {kind} from schema_version {version} to {version + 1}",
            )
        data = {**step(data), "schema_version": version + 1}
        version += 1
    try:
        return Result.of(expected.model_validate(data))
    except ValidationError as exc:
        return Result(
            None,
            tuple(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="record.invalid",
                    message=error["msg"],
                    location=tuple(error["loc"]),
                )
                for error in exc.errors()
            ),
        )


def _fail[R](code: str, message: str) -> Result[R]:
    return Result(None, (Diagnostic(severity=Severity.ERROR, code=code, message=message),))
