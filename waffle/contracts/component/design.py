"""Resolved designs, circuits of instances, and fabricated specimens."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from waffle.contracts.component.parameters import IDENTIFIER, DesignValues
from waffle.contracts.component.refs import DesignRef, ImplementationRef, TechnologyRef
from waffle.contracts.foundation.records import Record
from waffle.contracts.foundation.units import Quantity

_IDENTIFIER = IDENTIFIER.pattern


class ToolPin(BaseModel):
    """An exact version of the generator or backend that produced or checked a design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ResolvedDesign(Record):
    """An implementation with concrete design parameters and exactly pinned dependencies.

    It carries no operating conditions: those belong to an analysis, not to the device.
    """

    kind: Literal["design"] = "design"
    schema_version: Literal[1] = 1
    implementation: ImplementationRef
    parameters: DesignValues
    technology: TechnologyRef
    tools: tuple[ToolPin, ...] = ()


class Placement(BaseModel):
    """Minimal placement; geometric transforms are defined with Layout IR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: tuple[Quantity, Quantity] = (
        Quantity(value=0.0, unit="um"),
        Quantity(value=0.0, unit="um"),
    )
    rotation_deg: float = Field(default=0.0, allow_inf_nan=False)
    mirror: bool = False


class CircuitInstance(BaseModel):
    """A named use of a resolved design; many instances may share one design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_IDENTIFIER)
    design: DesignRef
    placement: Placement = Placement()


class Endpoint(BaseModel):
    """A terminal of a named instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance: str = Field(pattern=_IDENTIFIER)
    terminal: str = Field(pattern=_IDENTIFIER)

    def __str__(self) -> str:
        return f"{self.instance}.{self.terminal}"


class Connection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    a: Endpoint
    b: Endpoint


class Circuit(Record):
    """Instances and the connections between their terminals.

    Structure is checked by `waffle.design.instances.circuits.validate_circuit`, which
    needs the referenced interfaces.
    """

    kind: Literal["circuit"] = "circuit"
    schema_version: Literal[1] = 1
    instances: tuple[CircuitInstance, ...] = ()
    connections: tuple[Connection, ...] = ()


class SampleIdentity(BaseModel):
    """Where a physical sample came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lot: str = Field(min_length=1)
    wafer: str | None = None
    die: str | None = None
    site: str | None = None


class Specimen(Record):
    """One physical sample of a fabricated design; many specimens may share a design."""

    kind: Literal["specimen"] = "specimen"
    schema_version: Literal[1] = 1
    design: DesignRef
    sample: SampleIdentity
    fabrication: dict[str, str] = Field(default_factory=dict)
