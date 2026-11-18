"""Attempt records: the append-only history of one try at an operation.

An attempt is created `started` and ends at most once, as `succeeded`,
`failed`, or `cancelled`. A started attempt with no outcome whose process is
gone was interrupted. Outputs staged but never published are reported as
`partial_outputs`; they are never artifacts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.results.artifact import SCHEMA_VERSION, ArtifactRefField
from waffle.contracts.results.provenance import ATTEMPT_ID, InputLink, OperationInfo


class AttemptState(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStart(BaseModel):
    """Written once when the attempt begins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    attempt_id: str = Field(pattern=ATTEMPT_ID.pattern)
    operation: OperationInfo
    inputs: tuple[InputLink, ...] = ()
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    started_at: datetime


class AttemptOutcome(BaseModel):
    """Written once when the attempt ends. `produced` lists artifacts published before the end."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    state: Literal[AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.CANCELLED]
    finished_at: datetime
    diagnostics: tuple[Diagnostic, ...] = ()
    produced: tuple[ArtifactRefField, ...] = ()


class AttemptRecord(BaseModel):
    """An attempt as read back: its start, its outcome (if any), and leftover staged output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AttemptStart
    outcome: AttemptOutcome | None = None
    partial_outputs: tuple[str, ...] = ()

    @property
    def state(self) -> AttemptState:
        return AttemptState.STARTED if self.outcome is None else self.outcome.state
