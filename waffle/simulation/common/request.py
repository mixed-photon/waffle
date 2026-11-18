"""What to prepare: the request that `prepare_physical_problem` turns into a problem.

This is not the analysis request (excitations, observables, numerical settings): it only
chooses how the layout and technology are described physically.

* `dimensionality` is `3d` (extruded stack, nothing approximated) or `2d`. A `2d` request
  must carry an explicit `reduction`; a `3d` request must not. There is no default.
* `margins.lateral` is added on all four sides of the bounding box of the patterned
  geometry; `margins.vertical` is added above and below the vertical extent of the
  patterned levels. For 3D this is the domain's vertical extent; for 2D it is the window the
  vertical stacks are cut to for the reduction.
* `window` sets an explicit 3D domain in the selected cell's dbu and stack picometres.
  Supply exactly one of `margins` and `window`.
* `boundaries` overrides faces (default: absorbing, solver-chosen thickness). `z_*` faces
  apply to 3D only.
* `ports` selects layout port paths (`name`, `inst/name`); the default is the ports of the
  prepared cell itself (paths without `/`) that fit inside the explicit window, if supplied.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from waffle.contracts.component.refs import DesignRef
from waffle.contracts.foundation.records import EntityIdField
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.transform import Point
from waffle.contracts.layout.layout import Name
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import BoundaryCondition, Face


class Margins(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lateral: Quantity
    vertical: Quantity


class PhysicalWindow(BaseModel):
    """Exact limits of a 3D domain in layout dbu (x, y) and stack picometres (z)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: Point
    hi: Point
    z_min_pm: StrictInt
    z_max_pm: StrictInt

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lo[0] >= self.hi[0] or self.lo[1] >= self.hi[1]:
            raise ValueError("window needs lo < hi on both axes")
        if self.z_min_pm >= self.z_max_pm:
            raise ValueError("window needs z_min_pm < z_max_pm")
        return self


class EffectiveIndexSpec(BaseModel):
    """Reduce the vertical stack to effective indices at one vacuum `wavelength` for the
    slab mode of `polarization` and `mode_order` (0 is the fundamental)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wavelength: Quantity
    polarization: Polarization
    mode_order: int = Field(default=0, ge=0)


class PhysicalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityIdField
    dimensionality: Literal["2d", "3d"]
    margins: Margins | None = Field(default=None, exclude_if=lambda margins: margins is None)
    reduction: EffectiveIndexSpec | None = None
    boundaries: dict[Face, BoundaryCondition] = Field(default_factory=dict)
    cell: Name | None = None
    ports: tuple[str, ...] | None = None
    design: DesignRef | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    window: PhysicalWindow | None = Field(default=None, exclude_if=lambda window: window is None)

    @model_validator(mode="after")
    def _problem_id(self) -> Self:
        if self.id.kind != "physical_problem":
            raise ValueError(f"id {self.id} must have kind 'physical_problem'")
        return self
