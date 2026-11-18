"""Approximations a `PhysicalProblem` records instead of hiding.

A 3D problem that simplifies nothing has no approximations. A reduced 2D problem always has
one `EffectiveIndexReduction`, plus any further simplification made on the way.

Conventions. z is vertical (up from the substrate, as in the technology), x and y are in
the layout plane, and a slab mode propagates in the layout plane with the layers normal to
z. `TE` has the electric field parallel to the layers and perpendicular to the propagation
(E_y for propagation along x); `TM` has the magnetic field there (H_y, so E has a component
normal to the layers). Both are scalar-slab modes: `mode_order` 0 is the fundamental, the
one with the largest effective index. Lengths are exact integer picometres; indices are
real (lossless) and rounded to 12 significant digits so the record is reproducible.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from waffle.contracts.component.parameters import IDENTIFIER

_NAME = IDENTIFIER.pattern


class Polarization(StrEnum):
    TE = "te"
    TM = "tm"


class StackSegment(BaseModel):
    """`[z_min_pm, z_max_pm]` filled with `material`, whose index at the reduction
    wavelength is `index`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    z_min_pm: StrictInt
    z_max_pm: StrictInt
    material: str = Field(pattern=_NAME)
    index: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.z_min_pm >= self.z_max_pm:
            raise ValueError("segment needs z_min_pm < z_max_pm")
        return self


class ReducedColumn(BaseModel):
    """The vertical stack at one kind of lateral position and the index it was reduced to.

    `layer` is the drawn layer whose region this column describes (the column where exactly
    that layer is drawn), or None for the background where nothing is drawn. `segments`
    run bottom to top inside the reduction window; the first and last extend to infinity.
    `rule` says where `n_eff` came from: `guided_mode` is the slab mode of the stated
    polarization and order; `cladding_cutoff` means the column guides no such mode, so
    `n_eff` is the larger of the two outermost indices, the limit of the mode at cutoff.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: str | None
    segments: tuple[StackSegment, ...] = Field(min_length=1)
    n_eff: float = Field(gt=0, allow_inf_nan=False)
    rule: Literal["guided_mode", "cladding_cutoff"]


class EffectiveIndexReduction(BaseModel):
    """Vertical structure replaced by an effective index per lateral region.

    `method` and `method_version` identify the algorithm that produced the columns.
    `window_*` is the vertical interval the stacks were cut to; layers beyond it are
    replaced by the outermost one, extended to infinity. `validity` states what the number
    does not capture.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["effective_index"] = "effective_index"
    method: str = Field(min_length=1)
    method_version: StrictInt = Field(ge=1)
    polarization: Polarization
    mode_order: StrictInt = Field(ge=0)
    wavelength_pm: StrictInt = Field(gt=0)
    window_z_min_pm: StrictInt
    window_z_max_pm: StrictInt
    columns: tuple[ReducedColumn, ...] = Field(min_length=1)
    validity: str = Field(min_length=1)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.window_z_min_pm >= self.window_z_max_pm:
            raise ValueError("window needs window_z_min_pm < window_z_max_pm")
        return self

    def column(self, layer: str | None) -> ReducedColumn | None:
        return next((c for c in self.columns if c.layer == layer), None)


class SidewallAngleIgnored(BaseModel):
    """Levels whose non-vertical sidewalls were treated as vertical walls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["sidewall_ignored"] = "sidewall_ignored"
    levels: tuple[str, ...] = Field(min_length=1)


type Approximation = Annotated[
    EffectiveIndexReduction | SidewallAngleIgnored, Field(discriminator="type")
]
