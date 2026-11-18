"""Artifact references and manifests.

An artifact is an immutable bundle of named files described by a manifest. Its
reference `<kind>@sha256:<hex>` is the content hash of the canonical manifest,
which lists every file's size and digest, so identical content has an identical
reference. Entity-style revision references (`kind:name@sha256:<hex>`) always
contain a colon before the `@`; artifact references never do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator, model_validator

from waffle.contracts.foundation.canonical import canonical_json, content_hash
from waffle.contracts.foundation.identity import RevisionRef

SCHEMA_VERSION = 1

_KIND = re.compile(r"[a-z][a-z0-9_]*")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


@dataclass(frozen=True, order=True)
class ArtifactRef:
    """One published artifact: its `kind` and the content hash of its manifest."""

    kind: str
    digest: str

    def __post_init__(self) -> None:
        if not _KIND.fullmatch(self.kind):
            raise ValueError(f"invalid artifact kind {self.kind!r}")
        if not _DIGEST.fullmatch(self.digest):
            raise ValueError(f"invalid artifact digest {self.digest!r}")

    def __str__(self) -> str:
        return f"{self.kind}@{self.digest}"

    @classmethod
    def parse(cls, text: str) -> ArtifactRef:
        kind, sep, digest = text.partition("@")
        if not sep:
            raise ValueError(f"artifact ref {text!r} must look like 'kind@sha256:<hex>'")
        return cls(kind, digest)


type Reference = ArtifactRef | RevisionRef


def parse_reference(text: str) -> Reference:
    """Parse either reference form; the entity form is the one with a `:` before `@`."""
    head = text.partition("@")[0]
    return RevisionRef.parse(text) if ":" in head else ArtifactRef.parse(text)


def _to_reference(value: Any) -> Reference:
    if isinstance(value, (ArtifactRef, RevisionRef)):
        return value
    if isinstance(value, str):
        return parse_reference(value)
    raise ValueError(f"expected a reference string, got {type(value).__name__}")


def _to_artifact_ref(value: Any) -> ArtifactRef:
    if isinstance(value, ArtifactRef):
        return value
    if isinstance(value, str):
        return ArtifactRef.parse(value)
    raise ValueError(f"expected an artifact reference string, got {type(value).__name__}")


# Pydantic fields that hold references serialize as their string form.
ArtifactRefField = Annotated[
    ArtifactRef, PlainValidator(_to_artifact_ref), PlainSerializer(str, return_type=str)
]
ReferenceField = Annotated[
    Reference, PlainValidator(_to_reference), PlainSerializer(str, return_type=str)
]


class FileEntry(BaseModel):
    """One payload file. `dtype` and `shape` are set exactly for numpy arrays."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    size: int = Field(ge=0)
    digest: str = Field(pattern=_DIGEST.pattern)
    media_type: str = Field(min_length=1)
    dtype: str | None = None
    shape: tuple[int, ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not FILE_NAME.fullmatch(self.name):
            raise ValueError(f"invalid file name {self.name!r}")
        if (self.dtype is None) != (self.shape is None):
            raise ValueError("dtype and shape must be given together")
        return self


class ArtifactManifest(BaseModel):
    """Everything about an artifact except its bytes. `metadata` must be canonical-JSON-able."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    files: tuple[FileEntry, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not _KIND.fullmatch(self.kind):
            raise ValueError(f"invalid artifact kind {self.kind!r}")
        names = [f.name for f in self.files]
        if names != sorted(set(names)):
            raise ValueError("files must be unique and sorted by name")
        canonical_json(self.metadata)
        return self

    def file(self, name: str) -> FileEntry | None:
        return next((f for f in self.files if f.name == name), None)

    def canonical(self) -> str:
        """The exact text stored as `manifest.json`."""
        return canonical_json(self.model_dump(mode="json"))

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(self.kind, content_hash(self.model_dump(mode="json")))
