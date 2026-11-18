"""Provenance: which operation produced an artifact from which inputs.

Provenance is recorded beside the artifact, not inside its manifest. Identity
is content: the same bytes published by two attempts are one artifact, and each
production leaves its own immutable record.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.results.artifact import SCHEMA_VERSION, ArtifactRefField, ReferenceField

DEFAULT_ROLE = "input"
ATTEMPT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")


class InputLink(BaseModel):
    """`target` (an artifact or a record revision) was used in the given `role`.

    Roles are free-form lowercase words such as `input`, `fitting_data`,
    `validation_data`, or `reference`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str = Field(default=DEFAULT_ROLE, pattern=r"^[a-z][a-z0-9_]*$")
    target: ReferenceField


class OperationInfo(BaseModel):
    """The operation that ran: identity, parameters, and the environment it ran in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    version: str = Field(min_length=1)
    parameters_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment: dict[str, str] = Field(default_factory=dict)


def input_fingerprint(operation: OperationInfo, inputs: tuple[InputLink, ...]) -> str:
    """Hash of what determines an operation's result: name, version, parameters, inputs.

    The environment is excluded so attempts on different machines can be compared.
    """
    return content_hash(
        {
            "operation": operation.name,
            "version": operation.version,
            "parameters": operation.parameters_digest,
            "inputs": sorted(f"{link.role}={link.target}" for link in inputs),
        }
    )


class ProvenanceRecord(BaseModel):
    """`artifact` was produced by `operation` in attempt `attempt_id` from `inputs`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    artifact: ArtifactRefField
    attempt_id: str = Field(pattern=ATTEMPT_ID.pattern)
    operation: OperationInfo
    inputs: tuple[InputLink, ...] = ()
    recorded_at: datetime
