from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.geometry.shapes import Polygon, Rectangle
from waffle.contracts.layout.layout import Port
from waffle.geometry.services.shapes import to_polygon
from waffle.simulation.common.ports import check_port_geometry


def rect(x0: int, y0: int, x1: int, y1: int) -> Polygon:
    return to_polygon(Rectangle(lo=(x0, y0), hi=(x1, y1))).unwrap()


def port(x: int, y: int, orientation: int, width: int = 500) -> Port:
    return Port(
        name="p",
        center=(x, y),
        width=width,
        orientation=orientation,  # type: ignore[arg-type]
        layer="WG",
        domain=Domain.OPTICAL,
    )


def codes(p: Port, *polygons: Polygon) -> list[str]:
    return [d.code for d in check_port_geometry("p", p, polygons, subject="layout:x")]


STRIP = rect(0, -250, 10000, 250)


def test_ports_at_both_ends_of_a_strip_are_valid() -> None:
    assert codes(port(0, 0, 180), STRIP) == []
    assert codes(port(10000, 0, 0), STRIP) == []


def test_vertical_ports_on_a_vertical_strip_are_valid() -> None:
    column = rect(-250, 0, 250, 8000)
    assert codes(port(0, 8000, 90), column) == []
    assert codes(port(0, 0, 270), column) == []


def test_port_pointing_into_the_geometry_has_the_wrong_orientation() -> None:
    assert codes(port(0, 0, 0), STRIP) == ["physics.port.orientation"]
    assert codes(port(10000, 0, 180), STRIP) == ["physics.port.orientation"]


def test_port_inside_the_waveguide_or_off_it_is_not_on_the_geometry() -> None:
    assert codes(port(5000, 0, 0), STRIP) == ["physics.port.not_on_geometry"]
    assert codes(port(-500, 0, 180), STRIP) == ["physics.port.not_on_geometry"]
    assert codes(port(0, 400, 180), STRIP) == ["physics.port.not_on_geometry"]
    assert codes(port(0, 0, 180)) == ["physics.port.not_on_geometry"]


def test_port_width_must_equal_the_cross_section_exactly() -> None:
    assert codes(port(0, 0, 180, width=400), STRIP) == ["physics.port.width_mismatch"]
    assert codes(port(0, 0, 180, width=600), STRIP) == ["physics.port.width_mismatch"]
    # Off-centre by 50 nm: the segment 0..500 does not match -250..250.
    assert codes(port(0, 250, 180, width=500), STRIP) == ["physics.port.width_mismatch"]


def test_odd_widths_compare_exactly() -> None:
    odd = rect(0, -250, 1000, 251)  # 501 wide, centre at y = 0.5
    assert codes(port(0, 0, 180, width=501), odd) == ["physics.port.width_mismatch"]


def test_abutting_polygons_form_one_cross_section() -> None:
    halves = (rect(0, -250, 5000, 0), rect(0, 0, 5000, 250))
    assert codes(port(0, 0, 180), *halves) == []


def test_a_polygon_abutting_the_end_makes_the_plane_interior() -> None:
    block = rect(-1000, -1000, 0, 1000)
    assert codes(port(0, 0, 180), STRIP, block) == ["physics.port.not_on_geometry"]
    # A narrower stub leaves the strip's end exposed beside it, but not at the centre.
    stub = rect(-1000, -100, 0, 100)
    assert codes(port(0, 0, 180), STRIP, stub) == ["physics.port.not_on_geometry"]
    assert codes(port(0, 175, 180, width=150), STRIP, stub) == []


def test_diagnostics_are_located_at_the_port() -> None:
    (diagnostic,) = check_port_geometry("in", port(5000, 0, 0), (STRIP,), subject="layout:x")
    assert diagnostic.location == ("ports", "in") and "'in'" in diagnostic.message
