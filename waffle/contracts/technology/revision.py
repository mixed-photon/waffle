"""The `technology` record: grid, layer table, process stack, and materials.

Stack conventions (shared with `waffle.technology`):

* The z axis points up from the substrate; z is measured in the unit of each `Quantity`
  and compared in SI. `z_min` is the bottom of a level, `z_min + thickness` its top.
* Levels are listed bottom to top and may touch but not overlap. Where no level covers a
  z interval inside the stack, the stack's `background` material fills it, and the
  background also fills everything above and below the stack.
* A `Blanket` level is unpatterned: its `material` fills the whole plane over its z range.
* A `Patterned` level holds `material` wherever any of its `layers` is drawn (the union of
  those layers' shapes) and `fill` elsewhere. A partial etch is expressed by stacking
  levels: for example a 90 nm slab level patterned by `WG` and `SLAB` below a 130 nm core
  level patterned by `WG`.
* A drawn shape's outline is its footprint at the bottom of the level. `sidewall_angle` is
  measured from the substrate plane (90 deg or `None` is vertical); below 90 deg the
  feature narrows toward the top.

Nothing here assumes a 2D reduction: a consumer that collapses the stack to an effective
index must record that choice itself.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from waffle.contracts.component.parameters import IDENTIFIER
from waffle.contracts.foundation.records import Record
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import Material, WavelengthRange

_NAME = IDENTIFIER.pattern


class LayerPurpose(StrEnum):
    """`drawing` layers define physical shapes; `annotation` layers (ports, device
    recognition, labels) never pattern the stack."""

    DRAWING = "drawing"
    ANNOTATION = "annotation"


class LayoutLayer(BaseModel):
    """A logical layout layer and its GDS layer/datatype pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME)
    gds_layer: int = Field(ge=0, le=65535)
    gds_datatype: int = Field(ge=0, le=65535)
    purpose: LayerPurpose = LayerPurpose.DRAWING
    description: str = ""


class Blanket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["blanket"] = "blanket"


class Patterned(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["patterned"] = "patterned"
    layers: tuple[str, ...] = Field(min_length=1)
    fill: str = Field(pattern=_NAME)


class ProcessLevel(BaseModel):
    """One z interval of the stack. `material` is where the pattern is present."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_NAME)
    z_min: Quantity
    thickness: Quantity
    material: str = Field(pattern=_NAME)
    pattern: Annotated[Blanket | Patterned, Field(discriminator="type")]
    sidewall_angle: Quantity | None = None

    @property
    def bottom_m(self) -> float:
        """Bottom in metres (ValueError if a unit is unknown; validate first)."""
        return self.z_min.si

    @property
    def top_m(self) -> float:
        """Top in metres (ValueError if a unit is unknown; validate first)."""
        return self.z_min.si + self.thickness.si


class ProcessStack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    levels: tuple[ProcessLevel, ...] = Field(min_length=1)
    background: str = Field(pattern=_NAME)


class TechnologyRevision(Record):
    """A complete technology: what a layout's layers mean physically.

    The database unit and manufacturing grid are exact integers in picometres. Descriptive
    text lives in `metadata`, `LayoutLayer.description` and `Material.source`;
    `waffle.technology.fingerprint` ignores those.
    """

    kind: Literal["technology"] = "technology"
    schema_version: Literal[1] = 1
    dbu_pm: int = Field(gt=0)
    manufacturing_grid_pm: int | None = Field(default=None, gt=0)
    optical_band: WavelengthRange
    layers: tuple[LayoutLayer, ...]
    stack: ProcessStack
    materials: tuple[Material, ...]

    def layer(self, name: str) -> LayoutLayer | None:
        return next((layer for layer in self.layers if layer.name == name), None)

    def material(self, name: str) -> Material | None:
        return next((m for m in self.materials if m.name == name), None)

    def level(self, name: str) -> ProcessLevel | None:
        return next((lv for lv in self.stack.levels if lv.name == name), None)
