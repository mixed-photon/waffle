"""The generators this adapter offers, built from gdsfactory primitives.

Names and versions are Waffle's, not gdsfactory's: `waffle.gdsfactory.<name>` at
`GENERATOR_VERSION`, which changes whenever a generator's geometry, ports, or parameters do.
Each generator draws on the logical layer `WG` of the technology's layer table (its GDS pair
comes from the table, never from gdsfactory's generic PDK) and puts its ports on `WG`.
Parameters are `Quantity` lengths converted exactly to the technology's database unit; a
value off the grid is an error. Coordinates are integer dbu; ports are added at positions
that are exact in dbu.

The shapes follow gdsfactory's `straight`, `mmi1x2` and `mmi2x2` (tapers of
`length_taper` from `width` to `width_taper` join the MMI body; the output tapers sit
`±(gap_mmi + width_taper) / 2` off the axis), but the MMIs are hierarchical: one taper cell
instanced two or three times (inputs unrotated, outputs rotated by 180 degrees).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import gdsfactory as gf
import kfactory as kf
import klayout.db as kdb

from waffle.contracts.component.parameters import DesignParameterSchema, DesignValues, ParameterSpec
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.geometry.services.grid import to_dbu
from waffle.layout.backend.interface import BackendCode, GeneratorInfo, diagnostic

GENERATOR_VERSION = "1.0.0"
LAYER = "WG"
Point = tuple[int, int]


def _um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def _length(name: str, description: str, default: float | None = None) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        dimension=Dimension.LENGTH,
        default=None if default is None else _um(default),
        minimum=Quantity(value=1, unit="nm"),
        description=description,
    )


class Canvas:
    """gdsfactory components on a private layout, drawn in integer dbu on one GDS layer."""

    def __init__(self, kcl: kf.KCLayout, layer: int, datatype: int) -> None:
        self.kcl = kcl
        self.layer = kcl.layer(layer, datatype)
        self.dbu_um = kcl.dbu

    def component(self, name: str) -> gf.Component:
        return gf.Component(name=name, kcl=self.kcl)

    def polygon(self, component: gf.Component, points: list[Point]) -> None:
        component.add_polygon(kdb.Polygon([kdb.Point(x, y) for x, y in points]), layer=self.layer)

    def port(
        self, component: gf.Component, name: str, centre: Point, orientation: int, width: int
    ) -> None:
        component.add_port(
            name=name,
            center=(centre[0] * self.dbu_um, centre[1] * self.dbu_um),
            width=width * self.dbu_um,
            orientation=orientation,
            layer=self.layer,
        )

    def place(
        self, parent: gf.Component, child: gf.Component, name: str, rotation: int, at: Point
    ) -> None:
        """Instance `child` in `parent` rotated by `rotation` degrees, then moved to `at`."""
        reference = parent.add_ref(child, name=name)
        reference.trans = kdb.Trans(rotation // 90, False, *at)


Builder = Callable[[Canvas, Mapping[str, int]], Result[gf.Component]]


def _odd(values: Mapping[str, int], *names: str) -> list[Diagnostic]:
    return [
        diagnostic(
            BackendCode.REQUEST_INVALID,
            f"{name} must be an even number of dbu to be symmetric (got {values[name]})",
            location=("values", name),
        )
        for name in names
        if values[name] % 2
    ]


def _straight(canvas: Canvas, v: Mapping[str, int]) -> Result[gf.Component]:
    if problems := _odd(v, "width"):
        return Result(None, tuple(problems))
    length, half = v["length"], v["width"] // 2
    c = canvas.component("straight")
    canvas.polygon(c, [(0, -half), (length, -half), (length, half), (0, half)])
    canvas.port(c, "o1", (0, 0), 180, v["width"])
    canvas.port(c, "o2", (length, 0), 0, v["width"])
    return Result.of(c)


def _mmi(inputs: int) -> Builder:
    """An MMI with `inputs` (1 or 2) input ports and two output ports."""
    name = f"mmi{inputs}x2"

    def build(canvas: Canvas, v: Mapping[str, int]) -> Result[gf.Component]:
        width, taper_width, taper_length = v["width"], v["width_taper"], v["length_taper"]
        body_length, body_width, gap = v["length_mmi"], v["width_mmi"], v["gap_mmi"]
        problems = _odd(v, "width", "width_taper", "width_mmi")
        if (gap + taper_width) % 2:
            problems.append(
                diagnostic(
                    BackendCode.REQUEST_INVALID,
                    "gap_mmi + width_taper must be an even number of dbu so the output "
                    "tapers sit symmetrically about the axis",
                    location=("values", "gap_mmi"),
                )
            )
        offset = (gap + taper_width) // 2
        if offset + taper_width // 2 > body_width // 2:
            problems.append(
                diagnostic(
                    BackendCode.REQUEST_INVALID,
                    "the output tapers must lie within the MMI body: "
                    "(gap_mmi + 2 * width_taper) / 2 <= width_mmi / 2",
                    location=("values", "width_mmi"),
                )
            )
        if problems:
            return Result(None, tuple(problems))
        taper = canvas.component(f"{name}_taper")
        half, wide = width // 2, taper_width // 2
        canvas.polygon(taper, [(0, -half), (taper_length, -wide), (taper_length, wide), (0, half)])
        canvas.port(taper, "o1", (0, 0), 180, width)
        canvas.port(taper, "o2", (taper_length, 0), 0, taper_width)
        top = canvas.component(name)
        canvas.polygon(
            top,
            [
                (0, -body_width // 2),
                (body_length, -body_width // 2),
                (body_length, body_width // 2),
                (0, body_width // 2),
            ],
        )
        input_ys = (0,) if inputs == 1 else (-offset, offset)
        output_ys = (offset, -offset)
        for index, y in enumerate(input_ys, start=1):
            canvas.place(top, taper, f"in{index}", 0, (-taper_length, y))
            canvas.port(top, f"o{index}", (-taper_length, y), 180, width)
        for index, y in enumerate(output_ys, start=1):
            canvas.place(top, taper, f"out{index}", 180, (body_length + taper_length, y))
            canvas.port(top, f"o{inputs + index}", (body_length + taper_length, y), 0, width)
        return Result.of(top)

    return build


@dataclass(frozen=True)
class Generator:
    info: GeneratorInfo
    build: Builder


def _generator(
    name: str,
    description: str,
    parameters: tuple[ParameterSpec, ...],
    build: Builder,
) -> Generator:
    example = {
        spec.name: spec.default if spec.default is not None else _um(10.0) for spec in parameters
    }
    return Generator(
        GeneratorInfo(
            name=f"waffle.gdsfactory.{name}",
            version=GENERATOR_VERSION,
            description=description,
            parameters=DesignParameterSchema(parameters=parameters),
            example=DesignValues(values=example),
        ),
        build,
    )


_MMI_PARAMETERS = (
    _length("width", "width of the access waveguides (the MMI ports)", 0.5),
    _length("width_taper", "width where the access tapers meet the MMI body", 1.0),
    _length("length_taper", "length of each access taper", 10.0),
    _length("length_mmi", "length of the MMI body along x", 5.5),
    _length("width_mmi", "width of the MMI body along y", 2.5),
    _length("gap_mmi", "gap between neighbouring access tapers", 0.25),
)

GENERATORS: tuple[Generator, ...] = (
    _generator(
        "straight",
        "Straight strip waveguide from (0, 0) along +x; ports o1 (west) and o2 (east).",
        (
            _length("length", "length along x"),
            _length("width", "waveguide width", 0.5),
        ),
        _straight,
    ),
    _generator(
        "mmi1x2",
        "1x2 multimode interferometer: o1 west, o2 east upper, o3 east lower.",
        _MMI_PARAMETERS,
        _mmi(1),
    ),
    _generator(
        "mmi2x2",
        "2x2 multimode interferometer: o1 west lower, o2 west upper, o3 east upper, o4 east lower.",
        _MMI_PARAMETERS,
        _mmi(2),
    ),
)


def technology_grid(technology: TechnologyRevision) -> Result[Grid]:
    """The layout grid of `technology`: its database unit and manufacturing grid in dbu."""
    pm = technology.manufacturing_grid_pm
    if pm is not None and pm % technology.dbu_pm:
        return Result(
            None,
            (
                diagnostic(
                    BackendCode.GRID_MISMATCH,
                    f"manufacturing grid {pm} pm is not a multiple of the {technology.dbu_pm} pm "
                    "database unit",
                    subject=str(technology.id),
                ),
            ),
        )
    return Result.of(
        Grid(
            dbu_pm=technology.dbu_pm,
            manufacturing=1 if pm is None else pm // technology.dbu_pm,
        )
    )


def lengths_in_dbu(
    quantities: Mapping[str, Quantity], grid: Grid, subject: str
) -> Result[dict[str, int]]:
    """Every quantity as exact integer dbu; a value off the grid is an error."""
    found: list[Diagnostic] = []
    converted: dict[str, int] = {}
    for name, quantity in quantities.items():
        snap = to_dbu(quantity, grid, subject=subject, location=("values", name))
        found.extend(snap.diagnostics)
        if snap.value is not None:
            converted[name] = snap.value.dbu
    return Result.of(converted, found)
