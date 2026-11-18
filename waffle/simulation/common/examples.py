"""Small layouts and requests for tests of physics preparation (1 nm database unit; every
coordinate is a multiple of the synthetic technology's 5 nm manufacturing grid)."""

from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout, Port
from waffle.contracts.physics.approximations import Polarization
from waffle.simulation.common.request import (
    EffectiveIndexSpec,
    Margins,
    PhysicalRequest,
)

LAYERS = (
    LayerSpec(name="WG", layer=1, datatype=0),
    LayerSpec(name="SLAB", layer=2, datatype=0),
    LayerSpec(name="PORT", layer=1, datatype=10),
)


def _port(name: str, x: int, y: int, orientation: int, layer: str = "WG") -> Port:
    return Port(
        name=name,
        center=(x, y),
        width=500,
        orientation=orientation,  # type: ignore[arg-type]
        layer=layer,
        domain=Domain.OPTICAL,
    )


def _layout(name: str, top: str, cells: tuple[Cell, ...]) -> Layout:
    return Layout(
        id=EntityId("layout", name), grid=Grid(dbu_pm=1000), top=top, layers=LAYERS, cells=cells
    )


def straight_waveguide() -> Layout:
    """A 10 um long, 500 nm wide strip on WG from (0, -250) to (10000, 250), ports `o1`
    (facing -x) and `o2` (facing +x) at its ends."""
    cell = Cell(
        name="straight",
        shapes={"WG": (Rectangle(lo=(0, -250), hi=(10000, 250)),)},
        ports=(_port("o1", 0, 0, 180), _port("o2", 10000, 0, 0)),
    )
    return _layout("straight", "straight", (cell,))


def rib_waveguide() -> Layout:
    """The strip above on a 3 um wide SLAB region: a rib with a partial etch."""
    cell = Cell(
        name="rib",
        shapes={
            "WG": (Rectangle(lo=(0, -250), hi=(10000, 250)),),
            "SLAB": (Rectangle(lo=(0, -1500), hi=(10000, 1500)),),
        },
        ports=(_port("o1", 0, 0, 180), _port("o2", 10000, 0, 0)),
    )
    return _layout("rib", "rib", (cell,))


def hierarchical_bend() -> Layout:
    """Two instances of a 5 um strip: `a` along +x from the origin, `b` rotated 90 degrees
    and placed at (5000, 0), so the top cell is an L. Top ports `in` (0, 0) facing -x and
    `out` (5000, 5000) facing +y; the leaf's own ports reach the top as `a/o1`... `b/o2`."""
    leaf = Cell(
        name="leaf",
        shapes={"WG": (Rectangle(lo=(0, -250), hi=(5000, 250)),)},
        ports=(_port("o1", 0, 0, 180), _port("o2", 5000, 0, 0)),
    )
    top = Cell(
        name="bend",
        instances=(
            Instance(name="a", cell="leaf"),
            Instance(
                name="b", cell="leaf", transform=Transform(rotation=90, translation=(5000, 0))
            ),
        ),
        ports=(_port("in", 0, 0, 180), _port("out", 5000, 5000, 90)),
    )
    return _layout("bend", "bend", (top, leaf))


def request(
    dimensionality: str = "3d", *, name: str = "problem", **overrides: object
) -> PhysicalRequest:
    """A request with 2 um lateral and 1 um vertical margins; 2D uses TE0 at 1.55 um."""
    fields: dict[str, object] = {
        "id": EntityId("physical_problem", name),
        "dimensionality": dimensionality,
        "margins": Margins(
            lateral=Quantity(value=2, unit="um"), vertical=Quantity(value=1, unit="um")
        ),
    }
    if dimensionality == "2d":
        fields["reduction"] = EffectiveIndexSpec(
            wavelength=Quantity(value=1.55, unit="um"), polarization=Polarization.TE
        )
    return PhysicalRequest.model_validate({**fields, **overrides})
