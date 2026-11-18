from __future__ import annotations

from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.physics.problem import (
    Boundaries3D,
    BoundaryCondition,
    Domain3D,
    Extrusion3D,
    Face,
    Film,
    Prism,
)
from waffle.contracts.technology.materials import ConstantIndex, Material
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common.material_geometry import material_at, material_section


def _authored_scene() -> Extrusion3D:
    technology = synthetic_soi()
    coating = Material(
        name="coating",
        optical=ConstantIndex(n=1.8),
        validity=technology.optical_band,
        source="Synthetic coating for material precedence checks.",
    )
    return Extrusion3D(
        domain=Domain3D(
            lo=(-20, -20),
            hi=(20, 20),
            z_min_pm=-5_000,
            z_max_pm=7_000,
            boundaries=Boundaries3D(**{face.value: BoundaryCondition() for face in Face}),
        ),
        materials=(*technology.materials, coating),
        background="sio2",
        films=(
            Film(level="substrate", z_min_pm=-4_000, z_max_pm=0, material="si"),
            Film(level="coating", z_min_pm=-2_000, z_max_pm=1_000, material="coating"),
        ),
        prisms=(
            Prism(
                level="core",
                layer="WG",
                polygon=Polygon(points=((0, 0), (12, 0), (12, 12), (0, 12))),
                z_min_pm=0,
                z_max_pm=6_000,
                material="si",
                sidewall_angle_deg=45,
            ),
            Prism(
                level="overlap",
                layer="VIA",
                polygon=Polygon(points=((8, 3), (14, 3), (14, 9), (8, 9))),
                z_min_pm=1_000,
                z_max_pm=4_000,
                material="sio2",
            ),
        ),
    )


def test_directly_authored_solids_use_paint_order_and_unclipped_taper_origin() -> None:
    scene = _authored_scene()
    section = material_section(
        scene,
        1_000,
        axis="y",
        coordinate=6,
        positions=(2, 3, 8, 9, 10),
        heights_pm=(-5_000, -3_000, -1_000, 3_000, 4_000),
    ).unwrap()
    assert section.materials == (
        ("sio2",) * 5,
        ("si",) * 5,
        ("coating",) * 5,
        ("sio2", "si", "sio2", "sio2", "sio2"),
        ("sio2", "sio2", "si", "sio2", "sio2"),
    )
    assert material_at(scene, 1_000, (8, 6), 4_000).unwrap() == "si"


def test_material_inspection_rejects_outside_samples_and_empty_sections() -> None:
    scene = _authored_scene()
    for point, height in (((0, 0), 7_000), ((21, 0), 1_000)):
        result = material_at(scene, 1_000, point, height)
        assert result.value is None
        assert result.diagnostics[0].code == "physics.geometry.outside_domain"

    empty = material_section(
        scene, 1_000, axis="x", coordinate=0, positions=(), heights_pm=(0,)
    )
    assert empty.diagnostics[0].code == "physics.geometry.empty_section"


def test_concave_taper_reports_the_unsupported_prism() -> None:
    scene = _authored_scene()
    prism = scene.prisms[0].model_copy(
        update={
            "polygon": Polygon(points=((0, 0), (8, 0), (8, 4), (4, 4), (4, 8), (0, 8)))
        }
    )
    result = material_at(
        scene.model_copy(update={"prisms": (prism,)}), 1_000, (2, 2), 1_000
    )
    assert result.value is None
    assert result.diagnostics[0].code == "physics.geometry.unsupported_taper"
    assert result.diagnostics[0].location == ("prisms", 0, "polygon")
