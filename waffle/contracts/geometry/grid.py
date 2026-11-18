"""The database grid: the exact length of one integer coordinate step."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Grid(BaseModel):
    """Declares what an integer coordinate means.

    `dbu_pm` is the database unit in **integer picometres** (1000 = 1 nm). Picometres are
    the coarsest unit in which every length unit in `foundation.units` (m, mm, um, nm) and
    every practical database unit is an exact integer, so no floating point enters the
    declaration. `manufacturing` is the manufacturing grid as a multiple of the database
    unit: valid points have coordinates divisible by it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dbu_pm: StrictInt = Field(gt=0)
    manufacturing: StrictInt = Field(default=1, gt=0)

    def on_manufacturing(self, value: int) -> bool:
        return value % self.manufacturing == 0
