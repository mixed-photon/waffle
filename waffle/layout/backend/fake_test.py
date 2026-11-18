from __future__ import annotations

import sys

import pytest

from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Layout
from waffle.layout.backend.conformance import CASES, Case, applicable_cases
from waffle.layout.backend.fake import FakeBackend
from waffle.layout.backend.interface import Capability, ImportRequest
from waffle.pdk.testing.synthetic import synthetic_soi


@pytest.mark.parametrize("case", applicable_cases(FakeBackend()), ids=lambda c: c.name)
def test_fake_backend_conforms(case: Case) -> None:
    case.run(FakeBackend(), synthetic_soi())


def test_cases_follow_declared_capabilities() -> None:
    names = {c.name for c in applicable_cases(FakeBackend())}
    assert "boolean_operations" not in names and "merge_unions_overlapping_shapes" not in names
    assert {"generators_produce_valid_layouts", "gds_round_trip_preserves_layout"} <= names
    assert {c.name for c in CASES if Capability.GEOMETRY in c.requires} == {
        "merge_unions_overlapping_shapes",
        "boolean_operations",
        "boolean_reports_what_the_ir_cannot_hold",
    }


class LosesPorts(FakeBackend):
    """Exports faithfully but forgets the ports of every cell on import."""

    def import_gds(self, data: bytes, request: ImportRequest) -> Result[Layout]:
        result = super().import_gds(data, request)
        if result.value is None:
            return result
        stripped = result.value.model_copy(
            update={"cells": tuple(c.model_copy(update={"ports": ()}) for c in result.value.cells)}
        )
        return Result(stripped, result.diagnostics)


def test_conformance_cases_can_fail() -> None:
    broken = {c.name: c for c in applicable_cases(LosesPorts())}["gds_round_trip_preserves_layout"]
    with pytest.raises(AssertionError, match="layout:nested"):
        broken.run(LosesPorts(), synthetic_soi())


def test_core_interface_does_not_import_an_upstream_library() -> None:
    assert not {"gdsfactory", "kfactory", "klayout", "beamz"} & set(sys.modules)


def test_fake_imports_only_what_it_exported() -> None:
    layout = nested_layout()
    request = ImportRequest(layers=layout.layers, layout_id=EntityId("layout", "x"))
    result = FakeBackend().import_gds(b"never exported", request)
    assert result.value is None
    assert [d.code for d in result.diagnostics] == ["layout.backend.import_failed"]
