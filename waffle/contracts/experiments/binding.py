"""Immutable experiment choices for each instance of one circuit revision."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.component.refs import DesignRef, ImplementationRef, InterfaceRef
from waffle.contracts.foundation.records import Record, RevisionRefField, ref_to
from waffle.contracts.models.capability import Analysis
from waffle.contracts.results.artifact import ArtifactRefField

CircuitRef = Annotated[RevisionRefField, ref_to("circuit")]
ComponentRef = Annotated[RevisionRefField, ref_to("component")]
ModelRef = Annotated[RevisionRefField, ref_to("model")]
MappingRef = Annotated[RevisionRefField, ref_to("view_mapping")]


class InstanceBinding(BaseModel):
    """Exact dependency pins for one circuit instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    instance: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    design: DesignRef
    implementation: ImplementationRef
    component: ComponentRef
    interface: InterfaceRef
    model: ModelRef
    mapping: MappingRef
    analysis: Analysis
    operating: OperatingValues
    requested_channels: tuple[tuple[str, str], ...] = ()
    evaluator_name: str | None = Field(default=None, min_length=1)
    evaluator_version: str | None = Field(default=None, min_length=1)
    payload_artifact: ArtifactRefField | None = None

    @model_validator(mode="after")
    def _payload_pin(self) -> Self:
        if (self.evaluator_name is None) != (self.evaluator_version is None):
            raise ValueError("evaluator name and version must be pinned together")
        if (self.evaluator_name is None) == (self.payload_artifact is None):
            raise ValueError("pin exactly one evaluator or artifact payload")
        return self


class ExperimentBinding(Record):
    """A complete selection; one entry per circuit instance."""

    kind: Literal["experiment_binding"] = "experiment_binding"
    schema_version: Literal[1] = 1
    circuit: CircuitRef
    instances: tuple[InstanceBinding, ...]

    @model_validator(mode="after")
    def _unique_instances(self) -> Self:
        names = [entry.instance for entry in self.instances]
        if len(names) != len(set(names)):
            raise ValueError("instance bindings must be unique")
        return self
