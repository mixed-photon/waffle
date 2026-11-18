"""Typed identifiers and content-addressed revision references.

An `EntityId` names a thing (`component:mmi_2x2`). A `RevisionRef` pins one
immutable revision of it by content hash (`component:mmi_2x2@sha256:<hex>`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from waffle.contracts.foundation.canonical import content_hash

_KIND = re.compile(r"[a-z][a-z0-9_]*")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, order=True)
class EntityId:
    """Stable identity: a lowercase `kind` and a `name` unique within that kind."""

    kind: str
    name: str

    def __post_init__(self) -> None:
        if not _KIND.fullmatch(self.kind):
            raise ValueError(f"invalid entity kind {self.kind!r}")
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid entity name {self.name!r}")

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"

    @classmethod
    def parse(cls, text: str) -> EntityId:
        kind, sep, name = text.partition(":")
        if not sep:
            raise ValueError(f"entity id {text!r} must look like 'kind:name'")
        return cls(kind, name)


@dataclass(frozen=True, order=True)
class RevisionRef:
    """An entity pinned to the content hash of one revision."""

    entity: EntityId
    digest: str

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.digest):
            raise ValueError(f"invalid revision digest {self.digest!r}")

    def __str__(self) -> str:
        return f"{self.entity}@{self.digest}"

    @classmethod
    def of(cls, entity: EntityId, payload: Any) -> RevisionRef:
        """Pin `entity` to the canonical content hash of `payload`."""
        return cls(entity, content_hash(payload))

    @classmethod
    def parse(cls, text: str) -> RevisionRef:
        entity, sep, digest = text.partition("@")
        if not sep:
            raise ValueError(f"revision ref {text!r} must look like 'kind:name@sha256:<hex>'")
        return cls(EntityId.parse(entity), digest)
