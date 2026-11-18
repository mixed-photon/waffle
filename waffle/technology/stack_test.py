from __future__ import annotations

import pytest

from waffle.contracts.foundation.units import Quantity
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.technology.stack import Segment, vertical_column

NM = 1e-9
UM = 1e-6


def columns(column: tuple[Segment, ...]) -> list[tuple[float, float, str]]:
    return [(round(s.z_min_m / NM), round(s.z_max_m / NM), s.material) for s in column]


def test_column_outside_every_pattern() -> None:
    assert columns(vertical_column(synthetic_soi(), ())) == [
        (-727000, -2000, "si"),
        (-2000, 2220, "sio2"),  # box, fills, and cladding are one oxide column
    ]


def test_column_through_the_waveguide_core() -> None:
    assert columns(vertical_column(synthetic_soi(), ["WG"])) == [
        (-727000, -2000, "si"),
        (-2000, 0, "sio2"),
        (0, 220, "si"),
        (220, 2220, "sio2"),
    ]


def test_column_through_the_slab_only() -> None:
    assert columns(vertical_column(synthetic_soi(), ["SLAB"])) == [
        (-727000, -2000, "si"),
        (-2000, 0, "sio2"),
        (0, 90, "si"),
        (90, 2220, "sio2"),
    ]


def test_annotation_layers_do_not_change_the_column() -> None:
    assert vertical_column(synthetic_soi(), ["PORT", "DEVREC"]) == vertical_column(
        synthetic_soi(), ()
    )


def test_gaps_inside_the_stack_hold_the_background() -> None:
    tech = synthetic_soi()
    lifted = tuple(
        lv.model_copy(update={"z_min": Quantity(value=1, unit="um")})
        if lv.name == "cladding"
        else lv
        for lv in tech.stack.levels
    )
    tech = tech.model_copy(
        update={
            "stack": tech.stack.model_copy(
                update={"levels": lifted, "background": "si"}  # a background that stands out
            )
        }
    )
    # The gap above the core (220 nm to 1 um) is background, which merges with the core.
    assert columns(vertical_column(tech, ["WG"]))[2:] == [(0, 1000, "si"), (1000, 3000, "sio2")]
    assert columns(vertical_column(tech, ())) == [
        (-727000, -2000, "si"),
        (-2000, 220, "sio2"),
        (220, 1000, "si"),
        (1000, 3000, "sio2"),
    ]


def test_unit_conversion_is_exact_in_si() -> None:
    column = vertical_column(synthetic_soi(), ["WG"])
    assert column[2].z_max_m == pytest.approx(0.22 * UM)
    assert column[0].z_min_m == pytest.approx(-727 * UM)


def test_unknown_layer_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="NOPE"):
        vertical_column(synthetic_soi(), ["NOPE"])
