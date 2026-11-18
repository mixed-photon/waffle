"""Versioned device-analysis intent, independent of any electromagnetic solver."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from waffle.contracts.foundation.canonical import canonical_json
from waffle.contracts.foundation.records import Record, RevisionRefField, ref_to
from waffle.contracts.physics.approximations import Polarization

PortPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*(/[A-Za-z0-9_][A-Za-z0-9_.$\-]*)*$"
    ),
]
ProblemRef = Annotated[RevisionRefField, ref_to("physical_problem")]
MappingRef = Annotated[RevisionRefField, ref_to("view_mapping")]


class ObservableKind(StrEnum):
    RAW_FIELD = "raw_field"
    RAW_FLUX = "raw_flux"
    SOURCE_NORMALIZED_FLUX = "source_normalized_flux"
    MODAL_SCATTERING = "modal_scattering"


class Excitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["point", "port_mode"]
    port: PortPath | None = None
    center_dbu: tuple[StrictInt, StrictInt] | None = None
    wavelength_pm: StrictInt = Field(gt=0)
    mode_order: StrictInt = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _location(self) -> Self:
        if self.kind == "point" and (self.center_dbu is None or self.port is not None):
            raise ValueError("point excitation needs center_dbu and no port")
        if self.kind == "port_mode" and (
            self.port is None or self.center_dbu is not None
        ):
            raise ValueError("port_mode excitation needs a port and no center_dbu")
        return self


class Observable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: ObservableKind
    port: PortPath | None = None
    field_component: Literal["Ex", "Ey", "Ez", "Hx", "Hy", "Hz"] | None = None
    source_index: StrictInt | None = Field(default=None, ge=0)
    output_channel: str | None = None
    excitation_channel: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind is ObservableKind.RAW_FIELD and self.field_component is None:
            raise ValueError("raw_field needs field_component")
        if self.kind is ObservableKind.RAW_FLUX and self.port is None:
            raise ValueError("raw_flux needs a port plane")
        if self.kind in {
            ObservableKind.SOURCE_NORMALIZED_FLUX,
            ObservableKind.MODAL_SCATTERING,
        }:
            if self.source_index is None:
                raise ValueError("normalized observable needs source_index")
        elif self.source_index is not None:
            raise ValueError("raw observable cannot have source_index")
        if self.kind is ObservableKind.MODAL_SCATTERING:
            if self.output_channel is None or self.excitation_channel is None:
                raise ValueError("modal_scattering needs both channels")
        elif self.output_channel is not None or self.excitation_channel is not None:
            raise ValueError("channels apply only to modal_scattering")
        return self


class NumericalSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    grid_spacing_pm: StrictInt = Field(gt=0)
    time_step_as: StrictInt = Field(gt=0)
    steps: StrictInt = Field(gt=0, le=100_000_000)


class ResourceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    cpu_cores: StrictInt = Field(default=1, gt=0)
    memory_bytes: StrictInt = Field(default=1_073_741_824, gt=0)
    wall_time_s: StrictInt = Field(default=600, gt=0)


class BackendSettings(BaseModel):
    """Exact backend and its own versioned settings payload; backend validates keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    backend: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1)
    schema_version: StrictInt = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        canonical_json(self.parameters)
        return self


class AnalysisRequest(Record):
    """A request pins its prepared physical problem and optional logical view mapping."""

    kind: Literal["analysis_request"] = "analysis_request"
    schema_version: Literal[1] = 1
    problem: ProblemRef
    mapping: MappingRef | None = None
    polarization: Polarization | None = None
    excitations: tuple[Excitation, ...] = Field(min_length=1)
    observables: tuple[Observable, ...] = Field(min_length=1)
    numerical: NumericalSettings
    resources: ResourceRequest = ResourceRequest()
    backend_settings: BackendSettings

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [observable.name for observable in self.observables]
        if len(names) != len(set(names)):
            raise ValueError("observable names must be unique")
        if any(
            o.source_index is not None and o.source_index >= len(self.excitations)
            for o in self.observables
        ):
            raise ValueError("observable source_index is out of range")
        return self
