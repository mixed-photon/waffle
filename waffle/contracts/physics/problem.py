"""The `physical_problem` record: what a solver is asked to simulate, and nothing else.

A problem is a material distribution, a simulation domain with boundary conditions, and
the physical ports where modes can be launched or monitored. It says nothing about
excitations, observables, meshing, or time stepping; those belong to the analysis request.

**Dimensionality is a tagged union.** `description` is either `Extrusion3D` (a full 3D
description: prisms and films with materials) or `Reduced2D` (in-plane regions with effective
indices, valid only through the `EffectiveIndexReduction` in `approximations`). A consumer
must handle each; neither converts to the other.

**Coordinates.** In-plane (x, y) geometry is exact integer database units of `dbu_pm`
picometres, as in the layout; z and thicknesses are exact integer picometres. Nothing is a
float except indices and angles, so serialization is deterministic and the revision stable.
x and y are the layout axes, z is the vertical (up) of the technology stack.

**Ports.** A port's `path` is the layout port path (`name`, or `inst/name` through
instances), so mappings can return to the layout port. `center` and `width` define the
reference plane: the segment through `center`, perpendicular to `orientation`, of length
`width` (in 3D a rectangle spanning `z_min_pm` to `z_max_pm`). `orientation` points out of
the device: a wave leaving the device through the port travels along it.

**Faces.** `Face` names the domain faces; `x_min` is the face at the smallest x.

Source references are exact `RevisionRef`s. The revision of the whole record changes with
anything; `waffle.simulation.common.fingerprint` hashes only the physical content.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from waffle.contracts.component.definition import Domain as SignalDomain
from waffle.contracts.component.parameters import IDENTIFIER
from waffle.contracts.component.refs import DesignRef, TechnologyRef
from waffle.contracts.foundation.records import Record, RevisionRefField, ref_to
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.geometry.transform import Orientation, Point
from waffle.contracts.layout.layout import Name
from waffle.contracts.physics.approximations import Approximation, EffectiveIndexReduction
from waffle.contracts.technology.materials import Material

_IDENTIFIER = IDENTIFIER.pattern
_PORT_PATH = r"^[A-Za-z0-9_][A-Za-z0-9_.$\-]*(/[A-Za-z0-9_][A-Za-z0-9_.$\-]*)*$"

LayoutRef = Annotated[RevisionRefField, ref_to("layout")]


class Face(StrEnum):
    X_MIN = "x_min"
    X_MAX = "x_max"
    Y_MIN = "y_min"
    Y_MAX = "y_max"
    Z_MIN = "z_min"
    Z_MAX = "z_max"


class BoundaryKind(StrEnum):
    """`absorbing`: outgoing waves leave without reflection (PML or equivalent).
    `periodic`: the face is identified with the opposite one. `pec`/`pmc`: perfect
    electric/magnetic conductor wall. `symmetric`: a mirror plane of structure and
    excitation; the field parity is chosen by the analysis."""

    ABSORBING = "absorbing"
    PERIODIC = "periodic"
    PEC = "pec"
    PMC = "pmc"
    SYMMETRIC = "symmetric"


class BoundaryCondition(BaseModel):
    """`thickness_pm` is the physical depth an absorbing layer occupies inside the domain;
    None leaves it to the solver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BoundaryKind = BoundaryKind.ABSORBING
    thickness_pm: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _thickness_only_when_absorbing(self) -> Self:
        if self.thickness_pm is not None and self.kind is not BoundaryKind.ABSORBING:
            raise ValueError("thickness_pm applies to absorbing boundaries only")
        return self


def unpaired_periodic(boundaries: Mapping[Face, BoundaryCondition]) -> list[str]:
    """Axes (`x`, `y`, `z`) where exactly one of the two faces is periodic."""
    return [
        axis
        for axis in "xyz"
        if (low := boundaries.get(Face(f"{axis}_min"))) is not None
        and (high := boundaries.get(Face(f"{axis}_max"))) is not None
        and (low.kind is BoundaryKind.PERIODIC) != (high.kind is BoundaryKind.PERIODIC)
    ]


class Boundaries2D(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    x_min: BoundaryCondition
    x_max: BoundaryCondition
    y_min: BoundaryCondition
    y_max: BoundaryCondition

    @model_validator(mode="after")
    def _periodic_pairs(self) -> Self:
        if unpaired_periodic({Face(k): v for k, v in self}):
            raise ValueError("a periodic face needs a periodic opposite face")
        return self


class Boundaries3D(Boundaries2D):
    z_min: BoundaryCondition
    z_max: BoundaryCondition


def _check_box(lo: Point, hi: Point) -> None:
    if lo[0] >= hi[0] or lo[1] >= hi[1]:
        raise ValueError("domain needs lo < hi on both axes")


class Domain2D(BaseModel):
    """The in-plane simulation rectangle `lo` to `hi` (dbu, inclusive faces)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: Point
    hi: Point
    boundaries: Boundaries2D

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        _check_box(self.lo, self.hi)
        return self


class Domain3D(BaseModel):
    """The simulation box: in-plane `lo` to `hi` (dbu) and `z_min_pm` to `z_max_pm`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: Point
    hi: Point
    z_min_pm: StrictInt
    z_max_pm: StrictInt
    boundaries: Boundaries3D

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        _check_box(self.lo, self.hi)
        if self.z_min_pm >= self.z_max_pm:
            raise ValueError("domain needs z_min_pm < z_max_pm")
        return self


class PortPlane(BaseModel):
    """A port reference plane (see the module docstring). `layer` is the layout layer the
    port sits on; its cross-section is the waveguide drawn there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(pattern=_PORT_PATH)
    layer: Name
    domain: SignalDomain
    center: Point
    width: StrictInt = Field(gt=0)
    orientation: Orientation


class Port3D(PortPlane):
    """A plane port with the vertical extent of the levels patterned by its layer."""

    z_min_pm: StrictInt
    z_max_pm: StrictInt

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.z_min_pm >= self.z_max_pm:
            raise ValueError("port needs z_min_pm < z_max_pm")
        return self


class Film(BaseModel):
    """`material` filling the whole plane over `[z_min_pm, z_max_pm]` (a blanket level, or
    the fill of a patterned level), cut to the domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: str = Field(pattern=_IDENTIFIER)
    z_min_pm: StrictInt
    z_max_pm: StrictInt
    material: str = Field(pattern=_IDENTIFIER)


class Prism(BaseModel):
    """`polygon` (dbu) extruded over `[z_min_pm, z_max_pm]` in `material`, on top of the
    films. The polygon is the footprint at `z_min_pm`; `sidewall_angle_deg` is measured from
    the substrate plane, None for vertical walls, and the outline narrows toward the top
    when below 90."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: str = Field(pattern=_IDENTIFIER)
    layer: Name
    polygon: Polygon
    z_min_pm: StrictInt
    z_max_pm: StrictInt
    material: str = Field(pattern=_IDENTIFIER)
    sidewall_angle_deg: float | None = Field(default=None, gt=0, lt=90)


class Extrusion3D(BaseModel):
    """Everything the stack contains inside the domain, as paint layers: `background`
    everywhere, then `films`, then `prisms`. Later entries within each list take
    precedence. A prism keeps its full footprint and base height across a domain cut.
    `materials` supplies the models used by the region so it is self-contained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimensionality: Literal["3d"] = "3d"
    domain: Domain3D
    materials: tuple[Material, ...]
    background: str = Field(pattern=_IDENTIFIER)
    films: tuple[Film, ...]
    prisms: tuple[Prism, ...]
    ports: tuple[Port3D, ...] = ()

    @model_validator(mode="after")
    def _materials_defined(self) -> Self:
        defined = {m.name for m in self.materials}
        used = {self.background, *(f.material for f in self.films)}
        used.update(p.material for p in self.prisms)
        if used - defined:
            raise ValueError(f"materials {sorted(used - defined)} are not defined")
        return self


class Region2D(BaseModel):
    """`polygons` (dbu) of drawn layer `layer`, with effective `index`. Regions are painted
    in order over the background; a later region overrides an earlier one where they
    overlap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: Name
    polygons: tuple[Polygon, ...] = Field(min_length=1)
    index: float = Field(gt=0, allow_inf_nan=False)


class Reduced2D(BaseModel):
    """An in-plane problem: `background_index` everywhere in the domain, then `regions`.
    The indices come from `EffectiveIndexReduction` (one column per region and for the
    background)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimensionality: Literal["2d"] = "2d"
    domain: Domain2D
    background_index: float = Field(gt=0, allow_inf_nan=False)
    regions: tuple[Region2D, ...]
    ports: tuple[PortPlane, ...] = ()


class PhysicalProblem(Record):
    """A physical problem prepared from one layout and one technology revision."""

    kind: Literal["physical_problem"] = "physical_problem"
    schema_version: Literal[1] = 1
    layout: LayoutRef
    top_cell: Name
    technology: TechnologyRef
    design: DesignRef | None = None
    dbu_pm: StrictInt = Field(gt=0)
    description: Annotated[Extrusion3D | Reduced2D, Field(discriminator="dimensionality")]
    approximations: tuple[Approximation, ...] = ()

    @model_validator(mode="after")
    def _approximations_match_dimensionality(self) -> Self:
        reductions = [a for a in self.approximations if isinstance(a, EffectiveIndexReduction)]
        description = self.description
        if isinstance(description, Extrusion3D):
            if reductions:
                raise ValueError("a 3D description cannot carry an effective-index reduction")
        else:
            if len(reductions) != 1:
                raise ValueError("a 2D description needs exactly one effective-index reduction")
            expected = {r.layer: r.index for r in description.regions}
            expected[None] = description.background_index
            actual = {c.layer: c.n_eff for c in reductions[0].columns}
            if expected != actual:
                raise ValueError("2D indices must equal the reduction's effective indices")
        ports = description.ports
        if len({p.path for p in ports}) != len(ports):
            raise ValueError("port paths must be unique")
        return self

    def metres(self, dbu: int) -> float:
        """`dbu` database units in metres (correctly rounded)."""
        return dbu * self.dbu_pm / 10**12
