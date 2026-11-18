"""Model revisions: identity, dependencies, and typed capability claims."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from waffle.contracts.component.parameters import (
    FittedCoefficients,
    OperatingConditionSchema,
)
from waffle.contracts.component.refs import DesignRef, ImplementationRef, InterfaceRef
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.records import Record, migration
from waffle.contracts.foundation.units import Dimension
from waffle.contracts.models.capability import Applicability, Capability
from waffle.contracts.results.artifact import ArtifactRefField, ReferenceField


class ArtifactPayload(BaseModel):
    """The model's content stored as a published artifact in the artifact store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["artifact"] = "artifact"
    artifact: ArtifactRefField


class EvaluatorPayload(BaseModel):
    """The model is computed by a named, versioned evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["evaluator"] = "evaluator"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class EvidenceKind(StrEnum):
    SIMULATION = "simulation"
    MEASUREMENT = "measurement"
    VALIDATION = "validation"


class EvidenceRef(BaseModel):
    """A typed reference to the artifact or record revision that supports a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    ref: ReferenceField


class Extension(BaseModel):
    """Legacy v1 extension data, retained but never interpreted as executable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    version: int = Field(ge=1)
    body: dict[str, JsonValue] = Field(default_factory=dict)


class ModelRevision(Record):
    """A model's identity and dependencies.

    Operating conditions are declared (a schema) and supplied per analysis;
    fitted coefficients are stored. Typed v2 claims use `capability` and
    `applicability_spec`; plural extension fields retain v1 data only.
    """

    kind: Literal["model"] = "model"
    schema_version: Literal[2] = 2
    interface: InterfaceRef
    implementations: tuple[ImplementationRef, ...] = ()
    designs: tuple[DesignRef, ...] = ()
    operating_conditions: OperatingConditionSchema = OperatingConditionSchema()
    coefficients: FittedCoefficients = FittedCoefficients()
    payload: Annotated[ArtifactPayload | EvaluatorPayload, Field(discriminator="type")]
    evidence: tuple[EvidenceRef, ...] = ()
    capability: Capability | None = None
    applicability_spec: Applicability | None = None
    # Legacy extension fields remain readable for v1 records. New executable
    # capabilities use the typed fields above.
    capabilities: tuple[Extension, ...] = ()
    applicability: tuple[Extension, ...] = ()

    def check_units(self) -> list[Diagnostic]:
        """Report coefficients whose unit is unknown."""
        found: list[Diagnostic] = []
        for name, quantity in self.coefficients.values.items():
            if quantity.dimension is None:
                found.extend(
                    quantity.diagnostics(
                        Dimension.DIMENSIONLESS,
                        subject=str(self.id),
                        location=("coefficients", "values", name),
                    )
                )
        return found


@migration("model", 1)
def _model_v1_to_v2(data: dict[str, object]) -> dict[str, object]:
    """Retain legacy extensions, but do not interpret their unvalidated bodies."""
    return data
