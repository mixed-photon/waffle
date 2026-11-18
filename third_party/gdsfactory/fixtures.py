"""Helpers shared by the adapter's real-backend tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

import klayout.db as kdb

from third_party.gdsfactory.backend import GdsfactoryBackend
from third_party.gdsfactory.convert import kfactory_ports, layout_from_klayout
from third_party.gdsfactory.generators import GENERATOR_VERSION, GENERATORS, Canvas
from third_party.gdsfactory.session import session
from waffle.contracts.component.definition import GeneratorSource
from waffle.contracts.component.parameters import DesignValues
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.layout.layout import Layout
from waffle.layout.backend.interface import GenerationRequest, layer_table
from waffle.pdk.testing.synthetic import synthetic_soi

TECHNOLOGY = synthetic_soi()
TABLE = layer_table(TECHNOLOGY)
GRID = Grid(dbu_pm=1000, manufacturing=5)


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def request(name: str, technology=TECHNOLOGY, **lengths_um: float) -> GenerationRequest:  # type: ignore[no-untyped-def]
    return GenerationRequest(
        generator=GeneratorSource(name=f"waffle.gdsfactory.{name}", version=GENERATOR_VERSION),
        values=DesignValues(values={k: um(v) for k, v in lengths_um.items()}),
        technology=technology,
    )


def generate(name: str, **lengths_um: float) -> Layout:
    """The layout of generator `name` with the given lengths in um (others default)."""
    return GdsfactoryBackend().generate(request(name, **lengths_um)).unwrap()


def convert_upstream(build: Callable[[Canvas], str], *, top_ports: bool = True) -> Result[Layout]:
    """Build components on a private layout with `build` (returning the top cell name) and
    convert them as the adapter does."""
    with session(TECHNOLOGY.dbu_pm) as kcl:
        top = build(Canvas(kcl, 1, 0))
        ports = kfactory_ports(kcl, TABLE, subject="layout:upstream")
        if ports.value is None:
            return Result(None, ports.diagnostics)
        return layout_from_klayout(
            kcl.layout,
            layout_id=EntityId("layout", "upstream"),
            grid=GRID,
            layers=TABLE,
            top=top,
            ports=ports.value if top_ports else {},
        )


def composite() -> Layout:
    """Two 1x2 MMIs joined by two straight waveguides; the second MMI is mirrored.

    The first MMI sits at the origin; the second is mirrored about the y axis and placed so
    its output ports meet the waveguide ends."""
    wire, mmi = GENERATORS[0], GENERATORS[1]
    mmi_values = {
        "width": 500,
        "width_taper": 1000,
        "length_taper": 10000,
        "length_mmi": 5500,
        "width_mmi": 2500,
        "gap_mmi": 250,
    }

    def build(canvas: Canvas) -> str:
        body = mmi.build(canvas, mmi_values).unwrap()
        arm = wire.build(canvas, {"length": 20000, "width": 500}).unwrap()
        top = canvas.component("mzi")
        canvas.place(top, body, "mmi_a", 0, (0, 0))
        placed = top.add_ref(body, name="mmi_b")
        placed.trans = kdb.Trans(2, True, 51000, 0)  # mirror about the y axis, then move
        canvas.place(top, arm, "arm_upper", 0, (15500, 625))
        canvas.place(top, arm, "arm_lower", 0, (15500, -625))
        canvas.port(top, "in", (-10000, 0), 180, 500)
        canvas.port(top, "out", (61000, 0), 0, 500)
        return top.name

    return convert_upstream(build).unwrap()


def run_python(code: str) -> str:
    """Run `code` in a fresh interpreter with this process's import path; returns stdout."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout
