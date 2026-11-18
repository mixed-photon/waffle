from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Polygon, Rectangle
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Cell, Layout, Port
from waffle.geometry.services.grid import Rounding
from waffle.layout.services.flatten import flatten
from waffle.layout.services.physical import to_physical
from waffle.layout.services.regrid import regrid
from waffle.layout.services.validate import validate_layout

HALF_NM = Grid(dbu_pm=500)
TWO_NM = Grid(dbu_pm=2000)


def test_regridding_to_a_finer_grid_is_exact_and_preserves_physical_geometry() -> None:
    layout = nested_layout()
    fine = regrid(layout, HALF_NM).unwrap()
    assert fine.grid == HALF_NM
    assert next(c for c in fine.cells if c.name == "leaf").ports[0].center == (0, 2)
    assert validate_layout(fine) == []
    before = to_physical(flatten(layout).unwrap(), layout.grid)
    after = to_physical(flatten(fine).unwrap(), fine.grid)
    assert before == after


def test_regridding_there_and_back_restores_the_same_revision() -> None:
    layout = nested_layout()
    for grid in (HALF_NM, Grid(dbu_pm=200), Grid(dbu_pm=1)):
        back = regrid(regrid(layout, grid).unwrap(), layout.grid).unwrap()
        assert back == layout
        assert revision_of(back) == revision_of(layout)


def test_ratio_that_does_not_divide_is_a_diagnostic_per_cell() -> None:
    result = regrid(nested_layout(), TWO_NM)
    assert result.value is None
    assert {(d.code, d.location) for d in result.diagnostics} == {
        ("geometry.grid.off_grid", ("cells", "leaf")),
        ("geometry.grid.off_grid", ("cells", "mid")),
    }


def test_snapping_is_explicit_and_reports_the_worst_error() -> None:
    result = regrid(nested_layout(), TWO_NM, Rounding.FLOOR)
    layout = result.unwrap()
    assert {d.code for d in result.diagnostics} == {"geometry.grid.snapped"}
    assert "worst error 1000.0 pm" in result.diagnostics[0].message
    leaf = next(c for c in layout.cells if c.name == "leaf")
    assert leaf.ports[0].center == (0, 0)  # (0, 1) nm floors to (0, 0) at 2 nm
    assert [p.name for p in leaf.ports] == ["o1", "o2"]


def test_collapsing_shapes_and_ports_are_errors() -> None:
    tiny = Cell(
        name="t",
        shapes={"WG": (Rectangle(lo=(0, 0), hi=(1, 1)), Polygon(points=((0, 0), (2, 0), (0, 2))))},
        ports=(
            Port(
                name="p", center=(0, 0), width=1, orientation=0, layer="WG", domain=Domain.OPTICAL
            ),
        ),
    )
    layout = Layout(
        id=nested_layout().id,
        grid=Grid(dbu_pm=1000),
        top="t",
        layers=nested_layout().layers,
        cells=(tiny,),
    )
    result = regrid(layout, TWO_NM, Rounding.FLOOR)
    assert result.value is None
    assert sorted(
        d.message for d in result.diagnostics if d.code == "geometry.grid.degenerate"
    ) == [
        "a rectangle on layer 'WG' collapses on the new grid",
        "port 'p' collapses on the new grid",
    ]
