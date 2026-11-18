r"""A three-level layout used by the layout tests and services.

top --m1 (rot 90, +(100,0))------> mid --l1 (mirror, +(10,0))--> leaf
    \--m2 (mirror+rot 180, +(0,50))-> mid
"""

from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Polygon, Rectangle
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout, Port

LAYERS = (LayerSpec(name="WG", layer=1, datatype=0), LayerSpec(name="M1", layer=41, datatype=0))

LEAF = Cell(
    name="leaf",
    shapes={"WG": (Polygon(points=((0, 0), (4, 0), (0, 2))),)},
    ports=(
        Port(name="o1", center=(0, 1), width=2, orientation=180, layer="WG", domain=Domain.OPTICAL),
        Port(name="o2", center=(2, 0), width=4, orientation=270, layer="WG", domain=Domain.OPTICAL),
    ),
)

MID = Cell(
    name="mid",
    shapes={"M1": (Rectangle(lo=(0, 0), hi=(20, 5)),)},
    instances=(
        Instance(name="l1", cell="leaf", transform=Transform(mirror=True, translation=(10, 0))),
    ),
    ports=(
        Port(
            name="e1", center=(20, 2), width=3, orientation=0, layer="M1", domain=Domain.ELECTRICAL
        ),
    ),
)

TOP = Cell(
    name="top",
    instances=(
        Instance(name="m1", cell="mid", transform=Transform(rotation=90, translation=(100, 0))),
        Instance(
            name="m2",
            cell="mid",
            transform=Transform(mirror=True, rotation=180, translation=(0, 50)),
        ),
    ),
    ports=(
        Port(name="in", center=(0, 0), width=2, orientation=180, layer="WG", domain=Domain.OPTICAL),
    ),
)


def nested_layout(name: str = "nested") -> Layout:
    """The hierarchy above on a 1 nm grid; also the fixture for flatten and compare tests."""
    return Layout(
        id=EntityId("layout", name),
        grid=Grid(dbu_pm=1000),
        top="top",
        layers=LAYERS,
        cells=(TOP, MID, LEAF),
    )
