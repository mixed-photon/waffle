from __future__ import annotations

from waffle.contracts.component.design import Placement
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.transform import Transform
from waffle.geometry.services.grid import Rounding
from waffle.geometry.services.transforms import (
    manhattan_angle,
    placement_to_transform,
    transform_from_parts,
)

NM = Grid(dbu_pm=1000)


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def test_manhattan_angles_normalize_any_multiple_of_90() -> None:
    for degrees, expected in {
        0.0: 0,
        90.0: 90,
        -90.0: 270,
        450.0: 90,
        -360.0: 0,
        180.0: 180,
    }.items():
        assert manhattan_angle(degrees).unwrap() == expected


def test_non_manhattan_angle_is_unsupported() -> None:
    for degrees in (45.0, 90.0000001, 1e-9):
        result = manhattan_angle(degrees, subject="s", location=("r",))
        assert result.value is None
        assert [d.code for d in result.diagnostics] == ["geometry.unsupported.rotation"]


def test_transform_from_parts_reports_every_unsupported_part() -> None:
    ok = transform_from_parts((5, 6), 270.0, True).unwrap()
    assert ok == Transform(mirror=True, rotation=270, translation=(5, 6))
    bad = transform_from_parts((0, 0), 30.0, False, magnification=2.0)
    assert bad.value is None
    assert [d.code for d in bad.diagnostics] == [
        "geometry.unsupported.rotation",
        "geometry.unsupported.magnification",
    ]


def test_placement_converts_to_a_transform_on_the_grid() -> None:
    placement = Placement(origin=(um(1.5), um(-0.25)), rotation_deg=90.0, mirror=True)
    assert placement_to_transform(placement, NM).unwrap() == Transform(
        mirror=True, rotation=90, translation=(1500, -250)
    )
    assert placement_to_transform(Placement(), NM).unwrap() == Transform()


def test_placement_origin_must_be_on_the_grid_unless_snapping_is_requested() -> None:
    placement = Placement(origin=(um(0.0004), um(1.0)))
    result = placement_to_transform(placement, NM)
    assert result.value is None
    assert [(d.code, d.location) for d in result.diagnostics] == [
        ("geometry.grid.off_grid", ("origin", 0))
    ]
    snapped = placement_to_transform(placement, NM, Rounding.NEAREST)
    assert snapped.unwrap().translation == (0, 1000)
    assert [d.code for d in snapped.diagnostics] == ["geometry.grid.snapped"]


def test_placement_with_unsupported_angle_and_off_grid_origin_reports_both() -> None:
    placement = Placement(origin=(um(0.0004), um(0.0)), rotation_deg=45.0)
    result = placement_to_transform(placement, NM)
    assert [d.code for d in result.diagnostics] == [
        "geometry.grid.off_grid",
        "geometry.unsupported.rotation",
    ]
