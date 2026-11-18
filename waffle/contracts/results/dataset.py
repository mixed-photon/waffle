"""Immutable metadata for a bounded optical power measurement dataset."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from waffle.contracts.component.refs import DesignRef
from waffle.contracts.foundation.records import Record, RevisionRefField, ref_to
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.results.artifact import ArtifactRefField

SpecimenRef = Annotated[RevisionRefField, ref_to("specimen")]
MappingRef = Annotated[RevisionRefField, ref_to("view_mapping")]
DatasetRef = Annotated[RevisionRefField, ref_to("measurement_dataset")]
ChannelName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*$")
]


class WavelengthAxis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Literal["wavelength"] = "wavelength"
    values: tuple[float, ...] = Field(min_length=1, max_length=4096)
    unit: str


class PowerObservation(BaseModel):
    """Output and optional driven input are measurement channel names."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["optical_power"] = "optical_power"
    output_channel: ChannelName
    excitation_channel: ChannelName | None = None
    values: tuple[float, ...] = Field(min_length=1, max_length=4096)
    unit: str
    standard_deviation: tuple[float, ...] | None = None


class Calibration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    state: Literal["raw", "calibrated"] = "raw"
    method: str | None = None
    reference: str | None = None


class MeasurementPayload(BaseModel):
    """The accepted versioned JSON file, before publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    specimen: SpecimenRef
    design: DesignRef
    mapping: MappingRef
    axis: WavelengthAxis
    observations: tuple[PowerObservation, ...] = Field(min_length=1)
    conditions: dict[str, Quantity] = Field(default_factory=dict)
    calibration: Calibration = Calibration()
    synthetic: bool


class MeasurementDataset(Record):
    """Published metadata; raw bytes and current data live in immutable artifacts.

    Unknown uncertainty is represented by None in each observation. Zero is an
    explicitly measured/estimated zero and remains distinct after processing.
    """

    kind: Literal["measurement_dataset"] = "measurement_dataset"
    schema_version: Literal[1] = 1
    specimen: SpecimenRef
    design: DesignRef
    mapping: MappingRef
    raw_artifact: ArtifactRefField
    data_artifact: ArtifactRefField
    parent: DatasetRef | None = None
    axis: WavelengthAxis
    observations: tuple[PowerObservation, ...]
    conditions: dict[str, Quantity] = Field(default_factory=dict)
    calibration: Calibration = Calibration()
    synthetic: bool
