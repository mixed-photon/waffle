from __future__ import annotations

import struct

from waffle.contracts.layout.examples import nested_layout
from waffle.layout.formats.gds_stream import summarize
from waffle.layout.formats.testing import gds_bytes


def test_summary_of_nested_layout() -> None:
    summary = summarize(gds_bytes(nested_layout())).unwrap()
    assert summary.dbu_pm == 1000
    assert set(summary.cells) == {"top", "mid", "leaf"}
    assert summary.top_cells == ("top",)
    assert summary.layers == {(1, 0), (41, 0)}
    assert summary.undefined == ()


def test_database_unit_is_exact_in_picometres() -> None:
    layout = nested_layout().model_copy(
        update={"grid": nested_layout().grid.model_copy(update={"dbu_pm": 250})}
    )
    assert summarize(gds_bytes(layout)).unwrap().dbu_pm == 250


def test_reference_to_undefined_cell_is_reported() -> None:
    layout = nested_layout()
    without_leaf = layout.model_copy(
        update={"cells": tuple(c for c in layout.cells if c.name != "leaf")}
    )
    summary = summarize(gds_bytes(without_leaf)).unwrap()
    assert summary.undefined == ("leaf",)
    assert summary.top_cells == ("top",)


def test_invalid_streams_are_diagnosed() -> None:
    good = gds_bytes(nested_layout())
    cases = {
        "empty": b"",
        "truncated": good[:-2],
        "no ENDLIB": good[:-4],
        "garbage": b"WAFFLE-FAKE-GDS" * 4,
        "odd length": struct.pack(">HBB", 5, 0, 2) + b"\0",
    }
    for label, data in cases.items():
        result = summarize(data)
        assert result.value is None, label
        assert [d.code for d in result.diagnostics] == ["gds.stream.invalid"], label
