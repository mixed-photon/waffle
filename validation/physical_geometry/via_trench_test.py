"""Validate a synthetic via and sloped trench against calculated material boundaries."""

from __future__ import annotations

import pytest

from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Rectangle
from waffle.contracts.layout.layout import Cell, Instance, InstanceArray, LayerSpec, Layout
from waffle.contracts.physics.problem import Extrusion3D
from waffle.contracts.technology.materials import ConstantIndex, Material, WavelengthRange
from waffle.contracts.technology.revision import (
    Blanket,
    LayoutLayer,
    Patterned,
    ProcessLevel,
    ProcessStack,
    TechnologyRevision,
)
from waffle.simulation.common.material_geometry import material_at, material_section
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.common.request import Margins, PhysicalRequest, PhysicalWindow

DBU_PM = 1000
MASK_LAYERS = (
    LayerSpec(name="VIA", layer=1, datatype=0),
    LayerSpec(name="TRENCH", layer=2, datatype=0),
)
FULL_MARGINS = Margins(
    lateral=Quantity(value=100, unit="nm"), vertical=Quantity(value=100, unit="nm")
)


def _nm(value: int) -> Quantity:
    return Quantity(value=value, unit="nm")


@pytest.fixture
def via_trench() -> tuple[Layout, TechnologyRevision]:
    unit = Cell(
        name="unit",
        shapes={
            "VIA": (Rectangle(lo=(35, -25), hi=(95, 25)),),
            "TRENCH": (Rectangle(lo=(0, -100), hi=(200, 100)),),
        },
    )
    top = Cell(
        name="top",
        instances=(
            Instance(
                name="repeat",
                cell=unit.name,
                array=InstanceArray(columns=3, rows=1, column_step=(500, 0), row_step=(0, 0)),
            ),
        ),
    )
    layout = Layout(
        id=EntityId("layout", "via_trench"),
        grid=Grid(dbu_pm=DBU_PM),
        top=top.name,
        layers=MASK_LAYERS,
        cells=(top, unit),
    )
    band = WavelengthRange(minimum=_nm(1500), maximum=_nm(1600))
    technology = TechnologyRevision(
        id=EntityId("technology", "via_trench"),
        dbu_pm=DBU_PM,
        optical_band=band,
        layers=tuple(
            LayoutLayer(name=layer.name, gds_layer=layer.layer, gds_datatype=layer.datatype)
            for layer in MASK_LAYERS
        ),
        materials=tuple(
            Material(
                name=name,
                optical=ConstantIndex(n=n, k=k),
                validity=band,
                source="Synthetic integration fixture",
            )
            for name, n, k in (
                ("air", 1, 0),
                ("oxide", 1.45, 0),
                ("silicon", 3.5, 0),
                ("metal", 1, 3),
            )
        ),
        stack=ProcessStack(
            background="air",
            levels=(
                ProcessLevel(
                    name="substrate",
                    z_min=_nm(-140),
                    thickness=_nm(40),
                    material="silicon",
                    pattern=Blanket(),
                ),
                ProcessLevel(
                    name="via",
                    z_min=_nm(-100),
                    thickness=_nm(60),
                    material="metal",
                    pattern=Patterned(layers=("VIA",), fill="oxide"),
                ),
                ProcessLevel(
                    name="trench",
                    z_min=_nm(-40),
                    thickness=_nm(100),
                    material="air",
                    pattern=Patterned(layers=("TRENCH",), fill="oxide"),
                    sidewall_angle=Quantity(value=45, unit="deg"),
                ),
            ),
        ),
    )
    return layout, technology


def _prepare(
    fixture: tuple[Layout, TechnologyRevision], *, window: PhysicalWindow | None = None
) -> Extrusion3D:
    layout, technology = fixture
    request = PhysicalRequest(
        id=EntityId("physical_problem", "via_trench"),
        dimensionality="3d",
        margins=FULL_MARGINS if window is None else None,
        ports=(),
        window=window,
    )
    physical = prepare_physical_problem(layout, technology, request).unwrap()
    assert physical.layout == revision_of(layout)
    assert physical.technology == revision_of(technology)
    assert physical.dbu_pm == DBU_PM
    assert isinstance(physical.description, Extrusion3D)
    return physical.description


def test_local_via_and_trench_dimensions_and_material_boundaries(
    via_trench: tuple[Layout, TechnologyRevision],
) -> None:
    full = _prepare(via_trench)
    window = PhysicalWindow(lo=(30, -40), hi=(170, 40), z_min_pm=-141_000, z_max_pm=80_000)
    local = _prepare(via_trench, window=window)
    assert (local.domain.lo, local.domain.hi) == ((30, -40), (170, 40))
    assert (local.domain.z_min_pm, local.domain.z_max_pm) == (-141_000, 80_000)
    assert [(f.level, f.z_min_pm, f.z_max_pm, f.material) for f in local.films] == [
        ("substrate", -140_000, -100_000, "silicon"),
        ("via", -100_000, -40_000, "oxide"),
        ("trench", -40_000, 60_000, "oxide"),
    ]
    assert len(full.prisms) == 6
    via, trench = local.prisms
    assert (via.layer, via.material, via.z_min_pm, via.z_max_pm, via.sidewall_angle_deg) == (
        "VIA",
        "metal",
        -100_000,
        -40_000,
        None,
    )
    assert via.polygon.points == ((35, -25), (95, -25), (95, 25), (35, 25))
    assert (
        trench.layer,
        trench.material,
        trench.z_min_pm,
        trench.z_max_pm,
        trench.sidewall_angle_deg,
    ) == (
        "TRENCH",
        "air",
        -40_000,
        60_000,
        45,
    )
    assert trench.polygon.points == ((0, -100), (200, -100), (200, 100), (0, 100))

    # The 45-degree wall recedes by the height above -40 nm. At z=0 it has
    # moved past the left window face to x=40 nm, and the right wall is at x=160 nm.
    positions = (31, 39, 40, 80, 100, 120, 160, 161)
    heights_pm = (
        -141_000,
        -140_000,
        -100_001,
        -100_000,
        -40_001,
        -40_000,
        -20_000,
        0,
        30_000,
        59_999,
        60_000,
    )
    ambient = ("air",) * len(positions)
    substrate = ("silicon",) * len(positions)
    via_row = ("oxide", "metal", "metal", "metal", "oxide", "oxide", "oxide", "oxide")
    expected = (
        ambient,
        substrate,
        substrate,
        via_row,
        via_row,
        ambient,
        ambient,
        ("oxide", "oxide", "air", "air", "air", "air", "air", "oxide"),
        ("oxide", "oxide", "oxide", "air", "air", "air", "oxide", "oxide"),
        ("oxide", "oxide", "oxide", "oxide", "air", "oxide", "oxide", "oxide"),
        ambient,
    )
    for geometry in (local, full):
        section = material_section(
            geometry, DBU_PM, axis="y", coordinate=0, positions=positions, heights_pm=heights_pm
        ).unwrap()
        assert section.materials == expected
        for point, z_pm, material in (
            ((65, -25), -70_000, "metal"),
            ((65, -26), -70_000, "oxide"),
            ((100, 30), 30_000, "air"),
            ((100, 31), 30_000, "oxide"),
        ):
            assert material_at(geometry, DBU_PM, point, z_pm).unwrap() == material


def test_cut_through_trench_keeps_full_mask_and_original_taper_origin(
    via_trench: tuple[Layout, TechnologyRevision],
) -> None:
    full = _prepare(via_trench)
    window = PhysicalWindow(lo=(30, -40), hi=(170, 40), z_min_pm=-20_000, z_max_pm=50_000)
    local = _prepare(via_trench, window=window)
    assert [(film.level, film.z_min_pm, film.z_max_pm) for film in local.films] == [
        ("trench", -20_000, 50_000),
    ]
    (trench,) = local.prisms
    assert trench in full.prisms

    # At z=30 nm the setback is 70 nm on each side of the 200 nm mask, so
    # the cross-section at x=100 nm spans y=-30 to +30 nm, including its walls.
    positions = (-39, -31, -30, 0, 30, 31, 39)
    heights_pm = (-20_000, 20_000, 30_000, 49_999)
    ambient = ("air",) * len(positions)
    expected = (
        ambient,
        ambient,
        ("oxide", "oxide", "air", "air", "air", "oxide", "oxide"),
        ("oxide", "oxide", "oxide", "air", "oxide", "oxide", "oxide"),
    )
    for geometry in (local, full):
        section = material_section(
            geometry, DBU_PM, axis="x", coordinate=100, positions=positions, heights_pm=heights_pm
        ).unwrap()
        assert section.materials == expected
