"""Exact correspondence between a component interface and its physical/data views."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from waffle.contracts.component.refs import InterfaceRef
from waffle.contracts.foundation.records import Record, RevisionRefField, ref_to
from waffle.contracts.geometry.transform import Orientation, Point, Transform

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*$")]
PortPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*(/[A-Za-z0-9_][A-Za-z0-9_.$\-]*)*$"
    ),
]
LayoutRef = Annotated[RevisionRefField, ref_to("layout")]
ProblemRef = Annotated[RevisionRefField, ref_to("physical_problem")]
ModelRef = Annotated[RevisionRefField, ref_to("model")]


class Normalization(StrEnum):
    """Amplitude convention; `power_wave` means squared magnitude is power."""

    POWER_WAVE = "power_wave"
    FIELD_AMPLITUDE = "field_amplitude"
    VOLTAGE_WAVE = "voltage_wave"


class ModalTransform(StrEnum):
    """Only identity is executable in MOD-007; others are recorded and rejected."""

    IDENTITY = "identity"
    CONJUGATE = "conjugate"
    MIXED_BASIS = "mixed_basis"


class PortAnchor(BaseModel):
    """Expected terminal location in the mapping frame, independent of port naming."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    center: Point
    orientation: Orientation


class ChannelBinding(BaseModel):
    """One logical terminal's physical and observable correspondences.

    `reference_plane_offset_dbu` is signed along the outward port orientation. It is
    recorded for de-embedding; validation does not apply a phase correction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    terminal: Name
    layout_port: PortPath
    anchor: PortAnchor
    simulation_port: PortPath
    mode: StrictInt = Field(default=0, ge=0)
    model_channel: Name
    measurement_channel: Name | None = None
    reference_plane_offset_dbu: StrictInt = 0
    normalization: Normalization = Normalization.POWER_WAVE
    modal_transform: ModalTransform = ModalTransform.IDENTITY


class ResponseStatus(StrEnum):
    COMPUTED = "computed"
    UNCOMPUTED = "uncomputed"


class ResponseChannel(BaseModel):
    """One model response element, indexed by output and excitation channels."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    output: Name
    excitation: Name
    status: ResponseStatus


class ViewMapping(Record):
    """Immutable mapping against exact revisions. Binding order is terminal order.

    `transform` maps layout/problem coordinates into the mapping frame and covers
    orthogonal rotations and reflections. Model channel order is the declared permutation
    of interface terminals; duplicate or incomplete permutations are service errors.
    """

    kind: Literal["view_mapping"] = "view_mapping"
    schema_version: Literal[1] = 1
    interface: InterfaceRef
    layout: LayoutRef
    problem: ProblemRef
    model: ModelRef | None = None
    transform: Transform = Transform()
    bindings: tuple[ChannelBinding, ...]
    responses: tuple[ResponseChannel, ...] = ()
