"""Component interfaces, definitions, and physical implementations."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.component.parameters import IDENTIFIER, DesignParameterSchema
from waffle.contracts.component.refs import ComponentRef, InterfaceRef
from waffle.contracts.foundation.records import EntityIdField, Record
from waffle.contracts.results.artifact import ArtifactRefField


class Domain(StrEnum):
    OPTICAL = "optical"
    ELECTRICAL = "electrical"


class Terminal(BaseModel):
    """A named connection point; `role` is a free label such as `input` or `ground`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=IDENTIFIER.pattern)
    domain: Domain
    role: str | None = Field(default=None, pattern=IDENTIFIER.pattern)


class InterfaceDefinition(Record):
    """An ordered terminal set with its own revision, so models and mappings can
    reference exactly the interface they were written against."""

    kind: Literal["interface"] = "interface"
    schema_version: Literal[1] = 1
    terminals: tuple[Terminal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_terminals(self) -> Self:
        names = [t.name for t in self.terminals]
        if len(set(names)) != len(names):
            raise ValueError("terminal names must be unique")
        return self

    def terminal(self, name: str) -> Terminal | None:
        return next((t for t in self.terminals if t.name == name), None)


class ComponentDefinition(Record):
    """What a component is: an interface revision and the design parameters it takes."""

    kind: Literal["component"] = "component"
    schema_version: Literal[1] = 1
    interface: InterfaceRef
    design_parameters: DesignParameterSchema = DesignParameterSchema()


class GeneratorSource(BaseModel):
    """Layout produced by a named generator; `version` must be exact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["generator"] = "generator"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class LayoutArtifactSource(BaseModel):
    """Layout imported as a stored artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["layout_artifact"] = "layout_artifact"
    artifact: ArtifactRefField


class TechnologyRequirement(BaseModel):
    """The technology (by stable id) and layer or feature names an implementation needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    technology: EntityIdField
    layers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _is_technology(self) -> Self:
        if self.technology.kind != "technology":
            raise ValueError(f"{self.technology} is not a technology id")
        return self


class PhysicalImplementation(Record):
    """One way to realize a component physically."""

    kind: Literal["implementation"] = "implementation"
    schema_version: Literal[1] = 1
    component: ComponentRef
    source: Annotated[GeneratorSource | LayoutArtifactSource, Field(discriminator="type")]
    technology: TechnologyRequirement
