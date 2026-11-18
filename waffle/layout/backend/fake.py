"""An in-memory layout backend for tests: no upstream library, no geometry engine.

It generates one rectangle, writes structurally valid GDS bytes
(`waffle.layout.formats.testing.gds_bytes`), and imports only bytes it exported itself, by
remembering the layout behind each digest. It is enough to run the shared conformance cases
in the fast suite and to test the capability machinery; it says nothing about fidelity.
It offers no geometry operations.
"""

from __future__ import annotations

from waffle.contracts.component.definition import Domain
from waffle.contracts.component.parameters import DesignParameterSchema, DesignValues, ParameterSpec
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Severity, has_errors
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Rectangle
from waffle.contracts.layout.layout import Cell, Layout, Port
from waffle.geometry.services.grid import to_dbu
from waffle.layout.backend.interface import (
    BackendCode,
    BackendInfo,
    GenerationRequest,
    GeneratorInfo,
    ImportRequest,
    diagnostic,
    layer_table,
)
from waffle.layout.formats.sidecar import Producer, digest_of, verify_sidecar
from waffle.layout.formats.testing import gds_bytes
from waffle.layout.services.validate import validate_layout

NAME = "waffle.fake.rectangle"
VERSION = "1.0.0"
LAYER = "WG"


def _um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


GENERATOR = GeneratorInfo(
    name=NAME,
    version=VERSION,
    description="A rectangle on layer WG with a port on each short side.",
    parameters=DesignParameterSchema(
        parameters=(
            ParameterSpec(name="length", dimension=Dimension.LENGTH, minimum=_um(1)),
            ParameterSpec(name="width", dimension=Dimension.LENGTH, default=_um(2)),
        )
    ),
    example=DesignValues(values={"length": _um(10)}),
)


class FakeBackend:
    def __init__(self) -> None:
        self._exported: dict[str, Layout] = {}

    def info(self) -> BackendInfo:
        return BackendInfo(
            producer=Producer(name="waffle.fake", version=VERSION, upstream={"fake": "0"}),
            generators=(GENERATOR,),
            import_formats=("gds",),
            export_formats=("gds",),
        )

    def generate(self, request: GenerationRequest) -> Result[Layout]:
        source, technology = request.generator, request.technology
        if (source.name, source.version) != (NAME, VERSION):
            return Result(
                None,
                (
                    diagnostic(
                        BackendCode.GENERATOR_UNKNOWN,
                        f"generator {source.name} {source.version} is not offered",
                    ),
                ),
            )
        problems = GENERATOR.parameters.check(request.values, NAME)
        if problems:
            return Result(None, tuple(problems))
        if technology.layer(LAYER) is None:
            return Result(
                None,
                (diagnostic(BackendCode.LAYER_MISSING, f"technology has no layer {LAYER!r}"),),
            )
        grid = Grid(dbu_pm=technology.dbu_pm)
        values = GENERATOR.parameters.with_defaults(request.values.values)
        length = to_dbu(values["length"], grid)
        width = to_dbu(values["width"], grid)
        if length.value is None or width.value is None:
            return Result(None, (*length.diagnostics, *width.diagnostics))
        x, half = length.value.dbu, width.value.dbu // 2
        ports = tuple(
            Port(
                name=name,
                center=(cx, 0),
                width=2 * half,
                orientation=angle,
                layer=LAYER,
                domain=Domain.OPTICAL,
            )
            for name, cx, angle in (("o1", 0, 180), ("o2", x, 0))
        )
        layout_id = request.layout_id or EntityId(
            "layout", "fake_" + content_hash(request.values.model_dump(mode="json"))[7:19]
        )
        layout = Layout(
            id=layout_id,
            grid=grid,
            top="rectangle",
            layers=layer_table(technology),
            cells=(
                Cell(
                    name="rectangle",
                    shapes={LAYER: (Rectangle(lo=(0, -half), hi=(x, half)),)},
                    ports=ports,
                ),
            ),
            metadata={"generator": NAME, "generator_version": VERSION},
        )
        return Result.of(layout, validate_layout(layout))

    def export_gds(self, layout: Layout) -> Result[bytes]:
        problems = validate_layout(layout)
        if problems:
            return Result(None, tuple(problems))
        data = gds_bytes(layout)
        self._exported[digest_of(data)] = layout
        return Result.of(data)

    def import_gds(self, data: bytes, request: ImportRequest) -> Result[Layout]:
        if request.sidecar is not None:
            problems = verify_sidecar(data, request.sidecar)
            if has_errors(problems):
                return Result(None, tuple(problems))
        layout = self._exported.get(digest_of(data))
        if layout is None:
            return Result(
                None,
                (
                    diagnostic(
                        BackendCode.IMPORT_FAILED, "the fake backend imports only its own export"
                    ),
                ),
            )
        layout = layout.model_copy(update={"id": request.result_id})
        if request.sidecar is not None:
            return Result.of(layout)
        bare = layout.model_copy(
            update={
                "layers": request.layers,
                "cells": tuple(cell.model_copy(update={"ports": ()}) for cell in layout.cells),
            }
        )
        return Result.of(
            bare,
            [
                diagnostic(
                    BackendCode.NO_PORTS,
                    "no sidecar: ports are not imported",
                    severity=Severity.INFO,
                )
            ],
        )
