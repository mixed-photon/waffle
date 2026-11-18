"""What an independently distributed PDK supplies to Waffle.

A `Pdk` is a manifest plus the technology revisions it provides. Packages import only
these contract types. The manifest's `technologies` pins exactly the revisions the PDK
ships; the loader (`waffle.technology.registry`) checks that the two agree.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from waffle.contracts.component.refs import TechnologyRef
from waffle.contracts.foundation.records import Record
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.contracts.technology.versions import parse_specifier, parse_version


class Access(StrEnum):
    """Distribution class. Waffle records it and does not enforce access."""

    PUBLIC = "public"
    RESTRICTED = "restricted"


class DependencyPin(BaseModel):
    """An exact version (dotted integers) of a package the PDK needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str

    @field_validator("version")
    @classmethod
    def _exact(cls, value: str) -> str:
        parse_version(value)
        return value


class PdkManifest(Record):
    """Identity, compatibility, and contents of a PDK release."""

    kind: Literal["pdk"] = "pdk"
    schema_version: Literal[1] = 1
    version: str
    waffle: str  # compatible Waffle versions, e.g. ">=0.1,<0.2" (see versions.py)
    dependencies: tuple[DependencyPin, ...] = ()
    access: Access
    license: str = Field(min_length=1)
    technologies: tuple[TechnologyRef, ...]

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        parse_version(value)
        return value

    @field_validator("waffle")
    @classmethod
    def _specifier(cls, value: str) -> str:
        parse_specifier(value)
        return value


class Pdk(BaseModel):
    """A manifest and the technology revisions it provides."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: PdkManifest
    technologies: tuple[TechnologyRevision, ...]

    @model_validator(mode="after")
    def _nonempty(self) -> Self:
        if not self.technologies:
            raise ValueError("a PDK provides at least one technology")
        return self
