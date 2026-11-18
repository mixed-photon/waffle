"""Layout IR and technology to a `PhysicalProblem`.

Pipeline: validate the request, the technology and the layout; flatten the cell; map every
used layout layer through the technology; check the grid, the stack's extrusions and the
ports; then describe the physics in 3D (`Extrusion3D`) or reduced to 2D (`Reduced2D` with an
`EffectiveIndexReduction`). Any error returns no problem, with every diagnostic found.

Diagnostics added here, besides the `technology.*`, `layout.*`, `geometry.*`, and `units.*`
ones of the services it calls (an error never gets a problem; a warning does):

Request: `physics.reduction.required`,
`physics.reduction.unexpected`, `physics.margin.negative`, `physics.margin.nonpositive`,
`physics.margin.off_grid`, `physics.boundary.face_unsupported`,
`physics.boundary.periodic_unpaired`, `physics.length.sub_picometre`.

Layers (for layers the flattened cell uses): `physics.layer.unknown` (not in the technology),
`physics.layer.ambiguous` (several technology entries match the name or GDS pair),
`physics.layer.gds_mismatch` (layout and technology disagree on the pair),
`physics.layer.unpatterned` (error: shapes on a drawing layer that patterns no level; shapes
on annotation layers are ignored without a diagnostic), `physics.grid.incompatible` (error:
the layout dbu cannot represent the technology's manufacturing grid) and
`physics.grid.off_manufacturing` (warning, one per layer).

Stack: `physics.extrusion.thickness`, `physics.extrusion.material_unknown`.

Ports: `physics.port.unknown`, `physics.port.layer_unpatterned`, `physics.port.outside_domain`,
`physics.port.in_absorber` (centre closer to an in-plane face than that face's declared
absorber thickness), and from `ports.py` `physics.port.not_on_geometry`,
`physics.port.width_mismatch`, `physics.port.orientation`.

Domain and reduction: `physics.domain.empty`, `physics.reduction.layers_not_nested` (the
levels patterned by the drawn layers must be nested so one painted region per layer is
exact), plus `physics.reduction.*` from `waffle.simulation.photonics.device_analysis.reduction`.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.geometry.shapes import BBox, Polygon
from waffle.contracts.geometry.transform import Point
from waffle.contracts.layout.layout import Layout, Port
from waffle.contracts.physics.approximations import Approximation, SidewallAngleIgnored
from waffle.contracts.physics.problem import (
    Boundaries2D,
    Boundaries3D,
    BoundaryCondition,
    Domain2D,
    Domain3D,
    Extrusion3D,
    Face,
    Film,
    PhysicalProblem,
    Port3D,
    PortPlane,
    Prism,
    Reduced2D,
    Region2D,
    unpaired_periodic,
)
from waffle.contracts.technology.revision import (
    LayerPurpose,
    Patterned,
    ProcessLevel,
    TechnologyRevision,
)
from waffle.geometry.services.grid import pm_per_unit
from waffle.layout.services.flatten import FlatCell, flatten
from waffle.layout.services.validate import validate_layout
from waffle.simulation.common.ports import check_port_geometry
from waffle.simulation.common.request import EffectiveIndexSpec, PhysicalRequest
from waffle.simulation.photonics.device_analysis.reduction import Window, reduce_stack
from waffle.technology.mapping import map_layer
from waffle.technology.validate import validate_technology


@dataclass(frozen=True)
class _Level:
    """A stack level with exact picometre bounds."""

    level: ProcessLevel
    z_min_pm: int
    z_max_pm: int


@dataclass(frozen=True)
class _Request:
    lateral_dbu: int
    vertical_pm: int
    wavelength_pm: int | None
    faces: dict[Face, BoundaryCondition]


@dataclass(frozen=True)
class _Setup:
    """Everything the 2D and 3D builders share, all checked."""

    tech: TechnologyRevision
    levels: list[_Level]
    drawn: dict[str, tuple[Polygon, ...]]
    layer_levels: dict[str, tuple[ProcessLevel, ...]]
    ports: tuple[tuple[str, Port], ...]
    lo: Point
    hi: Point
    z_low: int
    z_high: int
    faces: dict[Face, BoundaryCondition]

    def plane(self, path: str, port: Port) -> PortPlane:
        return PortPlane(
            path=path,
            layer=port.layer,
            domain=port.domain,
            center=port.center,
            width=port.width,
            orientation=port.orientation,
        )


class _Findings:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.items: list[Diagnostic] = []

    def add(self, code: str, message: str, *location: str | int, warning: bool = False) -> None:
        self.items.append(
            Diagnostic(
                severity=Severity.WARNING if warning else Severity.ERROR,
                code=code,
                message=message,
                subject=self.subject,
                location=location,
            )
        )

    @property
    def failed(self) -> bool:
        return has_errors(self.items)


def prepare_physical_problem(
    layout: Layout, technology: TechnologyRevision, request: PhysicalRequest
) -> Result[PhysicalProblem]:
    """Describe `layout` (cell `request.cell` or its top) physically in `technology`."""
    found = _Findings(str(request.id))
    found.items.extend(validate_technology(technology))
    layout_problems = validate_layout(layout)
    found.items.extend(layout_problems)
    parsed = _parse_request(request, layout, found)
    levels = _stack_levels(technology, found)
    if has_errors(layout_problems):
        return Result(None, tuple(found.items))
    flat_result = flatten(layout, request.cell)
    if flat_result.value is None:
        return Result(None, (*found.items, *flat_result.diagnostics))
    flat = flat_result.value
    selected = _select_ports(flat, request, found)
    layer_levels = _map_layers(layout, technology, flat, selected, found)
    drawn = {name: polys for name, polys in flat.polygons.items() if layer_levels.get(name)}
    _check_grid(layout, technology, drawn, selected, found)
    for path, port in selected:
        if port.layer in layer_levels and not layer_levels[port.layer]:
            found.add(
                "physics.port.layer_unpatterned",
                f"port {path!r} is on layer {port.layer!r}, which patterns no stack level",
                "ports",
                path,
            )
        elif port.layer in layer_levels:
            found.items.extend(
                check_port_geometry(path, port, flat.polygons[port.layer], subject=found.subject)
            )
    box = BBox.of(p for polys in drawn.values() for poly in polys for p in poly.points)
    if box is None:
        found.add("physics.domain.empty", "no geometry on a layer that patterns the stack")
    if request.dimensionality == "2d":
        _check_nested(layer_levels, drawn, found)
    if parsed is None or levels is None or box is None:
        return Result(None, tuple(found.items))

    patterned = {lv.name for name in drawn for lv in layer_levels[name]}
    extent = [lv for lv in levels if lv.level.name in patterned]
    setup = _Setup(
        tech=technology,
        levels=levels,
        drawn=drawn,
        layer_levels=layer_levels,
        ports=tuple(selected),
        lo=(box.lo[0] - parsed.lateral_dbu, box.lo[1] - parsed.lateral_dbu),
        hi=(box.hi[0] + parsed.lateral_dbu, box.hi[1] + parsed.lateral_dbu),
        z_low=min(lv.z_min_pm for lv in extent) - parsed.vertical_pm,
        z_high=max(lv.z_max_pm for lv in extent) + parsed.vertical_pm,
        faces=parsed.faces,
    )
    _check_ports_in_domain(setup, layout.grid.dbu_pm, found)
    if found.failed:
        return Result(None, tuple(found.items))

    approximations: list[Approximation]
    if request.dimensionality == "3d":
        description: Extrusion3D | Reduced2D = _extrusion(setup)
        approximations = []
    else:
        assert request.reduction is not None and parsed.wavelength_pm is not None
        reduced = _reduced(setup, request.reduction, parsed.wavelength_pm)
        found.items.extend(reduced.diagnostics)
        if reduced.value is None:
            return Result(None, tuple(found.items))
        description, approximations = reduced.value
    problem = PhysicalProblem(
        id=request.id,
        metadata=request.metadata,
        layout=revision_of(layout),
        top_cell=flat.cell,
        technology=revision_of(technology),
        design=request.design,
        dbu_pm=layout.grid.dbu_pm,
        description=description,
        approximations=tuple(approximations),
    )
    return Result.of(problem, found.items)


def _exact_pm(
    quantity: Quantity, found: _Findings, *location: str | int, report_units: bool = True
) -> int | None:
    """`quantity` as an integer number of picometres, or None with a diagnostic."""
    if quantity.dimension is not Dimension.LENGTH:
        if report_units:
            found.items.extend(
                quantity.diagnostics(Dimension.LENGTH, subject=found.subject, location=location)
            )
        return None
    exact = Fraction(repr(quantity.value)) * pm_per_unit(quantity.unit)
    if exact.denominator != 1:
        found.add(
            "physics.length.sub_picometre",
            f"{quantity.value} {quantity.unit} is not a whole number of picometres",
            *location,
        )
        return None
    return int(exact)


def _parse_request(request: PhysicalRequest, layout: Layout, found: _Findings) -> _Request | None:
    """The request's quantities as exact integers, or None after reporting what is wrong."""
    before = len(found.items)
    reduction = request.reduction
    if request.dimensionality == "2d" and reduction is None:
        found.add(
            "physics.reduction.required",
            "a 2D problem needs an explicit effective-index reduction (wavelength, polarization)",
            "reduction",
        )
    if request.dimensionality == "3d" and reduction is not None:
        found.add("physics.reduction.unexpected", "a 3D problem is not reduced", "reduction")
    lateral = _exact_pm(request.margins.lateral, found, "margins", "lateral")
    vertical = _exact_pm(request.margins.vertical, found, "margins", "vertical")
    wavelength = (
        _exact_pm(reduction.wavelength, found, "reduction", "wavelength") if reduction else None
    )
    dbu = layout.grid.dbu_pm
    if lateral is not None and lateral < 0:
        found.add("physics.margin.negative", "lateral margin is negative", "margins", "lateral")
    elif lateral is not None and lateral % dbu:
        found.add(
            "physics.margin.off_grid",
            f"lateral margin {lateral} pm is not a multiple of the {dbu} pm database unit",
            "margins",
            "lateral",
        )
    if vertical is not None and vertical <= 0:
        found.add(
            "physics.margin.nonpositive", "vertical margin must be positive", "margins", "vertical"
        )
    if wavelength is not None and wavelength <= 0:
        found.add(
            "physics.length.sub_picometre", "wavelength must be positive", "reduction", "wavelength"
        )
    if request.dimensionality == "2d":
        for face in request.boundaries:
            if face in (Face.Z_MIN, Face.Z_MAX):
                found.add(
                    "physics.boundary.face_unsupported",
                    f"face {face.value} does not exist in a 2D problem",
                    "boundaries",
                    face.value,
                )
    faces = {face: request.boundaries.get(face, BoundaryCondition()) for face in Face}
    for axis in unpaired_periodic(faces):
        found.add(
            "physics.boundary.periodic_unpaired",
            f"exactly one {axis} face is periodic",
            "boundaries",
            axis,
        )
    if len(found.items) > before or vertical is None or lateral is None:
        return None
    return _Request(lateral // dbu, vertical, wavelength, faces)


def _stack_levels(tech: TechnologyRevision, found: _Findings) -> list[_Level] | None:
    """Levels with exact picometre bounds, bottom to top, or None if any level is unusable.
    Unit errors are `validate_technology`'s to report."""
    levels: list[_Level] = []
    defined = {m.name for m in tech.materials}
    for i, level in enumerate(tech.stack.levels):
        where = ("stack", "levels", i)
        z_min = _exact_pm(level.z_min, found, *where, "z_min", report_units=False)
        thickness = _exact_pm(level.thickness, found, *where, "thickness", report_units=False)
        pattern = level.pattern
        used = (level.material, pattern.fill if isinstance(pattern, Patterned) else level.material)
        for material in sorted(set(used) - defined):
            found.add(
                "physics.extrusion.material_unknown",
                f"level {level.name!r} uses undefined material {material!r}",
                *where,
            )
        if thickness is not None and thickness <= 0:
            found.add(
                "physics.extrusion.thickness",
                f"level {level.name!r} has non-positive thickness",
                *where,
                "thickness",
            )
        elif z_min is not None and thickness is not None:
            levels.append(_Level(level, z_min, z_min + thickness))
    if tech.stack.background not in defined:
        found.add(
            "physics.extrusion.material_unknown",
            f"stack background {tech.stack.background!r} is not a defined material",
            "stack",
            "background",
        )
    if len(levels) != len(tech.stack.levels):
        return None
    return sorted(levels, key=lambda lv: lv.z_min_pm)


def _select_ports(
    flat: FlatCell, request: PhysicalRequest, found: _Findings
) -> list[tuple[str, Port]]:
    available = {q.path: q.port for q in flat.ports}
    if request.ports is None:
        return [(path, port) for path, port in available.items() if "/" not in path]
    selected: list[tuple[str, Port]] = []
    for path in sorted(set(request.ports)):
        if path in available:
            selected.append((path, available[path]))
        else:
            found.add("physics.port.unknown", f"layout has no port {path!r}", "ports", path)
    return selected


def _map_layers(
    layout: Layout,
    tech: TechnologyRevision,
    flat: FlatCell,
    ports: list[tuple[str, Port]],
    found: _Findings,
) -> dict[str, tuple[ProcessLevel, ...]]:
    """The levels each used layer patterns (none for annotation layers). Layers that cannot
    be resolved are reported and left out."""
    resolved: dict[str, tuple[ProcessLevel, ...]] = {}
    specs = {spec.name: spec for spec in layout.layers}
    for name in sorted({*flat.polygons, *(port.layer for _, port in ports)}):
        where = ("layers", name)
        mapping = map_layer(tech, name)
        codes = {d.code for d in mapping.diagnostics}
        layer = tech.layer(name)
        if layer is None:
            found.add("physics.layer.unknown", f"layer {name!r} is not in the technology", *where)
            continue
        if "technology.layer_ambiguous" in codes:
            found.add(
                "physics.layer.ambiguous",
                f"layer {name!r} matches several technology layers",
                *where,
            )
            continue
        pair = (layer.gds_layer, layer.gds_datatype)
        spec = specs[name]
        if pair != (spec.layer, spec.datatype):
            found.add(
                "physics.layer.gds_mismatch",
                f"layer {name!r} is GDS {spec.layer}/{spec.datatype} in the layout but "
                f"{pair[0]}/{pair[1]} in the technology",
                *where,
            )
        elif sum((other.gds_layer, other.gds_datatype) == pair for other in tech.layers) > 1:
            found.add(
                "physics.layer.ambiguous",
                f"GDS pair {pair[0]}/{pair[1]} of layer {name!r} belongs to several "
                "technology layers",
                *where,
            )
        levels = mapping.value.levels if mapping.value else ()
        if not levels and layer.purpose is LayerPurpose.DRAWING and name in flat.polygons:
            found.add(
                "physics.layer.unpatterned",
                f"shapes are drawn on layer {name!r}, which patterns no stack level",
                *where,
            )
        resolved[name] = levels
    return resolved


def _check_grid(
    layout: Layout,
    tech: TechnologyRevision,
    drawn: dict[str, tuple[Polygon, ...]],
    ports: list[tuple[str, Port]],
    found: _Findings,
) -> None:
    reference = tech.manufacturing_grid_pm or tech.dbu_pm
    dbu = layout.grid.dbu_pm
    if reference % dbu:
        found.add(
            "physics.grid.incompatible",
            f"layout database unit {dbu} pm does not divide the technology grid {reference} pm",
            "grid",
        )
        return
    step = reference // dbu
    for name, polygons in drawn.items():
        off = sum(1 for poly in polygons for p in poly.points if p[0] % step or p[1] % step)
        if off:
            found.add(
                "physics.grid.off_manufacturing",
                f"{off} vertices of layer {name!r} are off the {reference} pm grid",
                "layers",
                name,
                warning=True,
            )
    for path, port in ports:
        if port.center[0] % step or port.center[1] % step:
            found.add(
                "physics.grid.off_manufacturing",
                f"port {path!r} center is off the {reference} pm grid",
                "ports",
                path,
                warning=True,
            )


def _painter_order(
    layer_levels: dict[str, tuple[ProcessLevel, ...]], drawn: dict[str, tuple[Polygon, ...]]
) -> list[str]:
    """Drawn layers by increasing number of patterned levels, then name."""
    return sorted(drawn, key=lambda name: (len(layer_levels[name]), name))


def _check_nested(
    layer_levels: dict[str, tuple[ProcessLevel, ...]],
    drawn: dict[str, tuple[Polygon, ...]],
    found: _Findings,
) -> None:
    """Where drawn layers overlap, the column is that of the layer patterning the most
    levels only if the others' levels are a subset of its own."""
    ordered = _painter_order(layer_levels, drawn)
    for lower, upper in zip(ordered, ordered[1:], strict=False):
        if not {lv.name for lv in layer_levels[lower]} <= {lv.name for lv in layer_levels[upper]}:
            found.add(
                "physics.reduction.layers_not_nested",
                f"layers {lower!r} and {upper!r} pattern levels that are not nested, so one "
                "effective index per layer would be wrong where they overlap",
                "layers",
                upper,
            )


def _check_ports_in_domain(setup: _Setup, dbu_pm: int, found: _Findings) -> None:
    """Ports must lie inside the domain and outside any absorber of declared thickness."""
    for path, port in setup.ports:
        a, b = (0, 1) if port.orientation in (0, 180) else (1, 0)
        centre = port.center
        inside = (
            setup.lo[a] <= centre[a] <= setup.hi[a]
            and 2 * setup.lo[b] <= 2 * centre[b] - port.width
            and 2 * centre[b] + port.width <= 2 * setup.hi[b]
        )
        if not inside:
            found.add(
                "physics.port.outside_domain",
                f"port {path!r} segment is not inside the domain {setup.lo} to {setup.hi}",
                "ports",
                path,
            )
            continue
        distances = {
            Face.X_MIN: centre[0] - setup.lo[0],
            Face.X_MAX: setup.hi[0] - centre[0],
            Face.Y_MIN: centre[1] - setup.lo[1],
            Face.Y_MAX: setup.hi[1] - centre[1],
        }
        for face, distance in distances.items():
            boundary = setup.faces[face]
            if boundary.thickness_pm is not None and distance * dbu_pm < boundary.thickness_pm:
                found.add(
                    "physics.port.in_absorber",
                    f"port {path!r} is {distance * dbu_pm} pm from face {face.value}, inside "
                    f"its {boundary.thickness_pm} pm absorber",
                    "ports",
                    path,
                )


def _deg(level: ProcessLevel) -> float | None:
    """The sidewall angle in degrees, None when vertical."""
    if level.sidewall_angle is None:
        return None
    degrees = float(f"{level.sidewall_angle.to('deg').value:.12g}")
    return None if degrees == 90.0 else degrees


def _extrusion(setup: _Setup) -> Extrusion3D:
    tech = setup.tech
    films: list[Film] = []
    prisms: list[Prism] = []
    for entry in setup.levels:
        pattern = entry.level.pattern
        low, high = max(entry.z_min_pm, setup.z_low), min(entry.z_max_pm, setup.z_high)
        fill = pattern.fill if isinstance(pattern, Patterned) else entry.level.material
        if low < high:
            films.append(Film(level=entry.level.name, z_min_pm=low, z_max_pm=high, material=fill))
        if isinstance(pattern, Patterned):
            for layer in sorted(pattern.layers):
                for polygon in setup.drawn.get(layer, ()):
                    prisms.append(
                        Prism(
                            level=entry.level.name,
                            layer=layer,
                            polygon=polygon,
                            z_min_pm=entry.z_min_pm,
                            z_max_pm=entry.z_max_pm,
                            material=entry.level.material,
                            sidewall_angle_deg=_deg(entry.level),
                        )
                    )
    names = {tech.stack.background, *(f.material for f in films), *(p.material for p in prisms)}
    by_level = {entry.level.name: entry for entry in setup.levels}
    ports = []
    for path, port in setup.ports:
        spans = [by_level[lv.name] for lv in setup.layer_levels[port.layer]]
        ports.append(
            Port3D(
                **setup.plane(path, port).model_dump(),
                z_min_pm=min(s.z_min_pm for s in spans),
                z_max_pm=max(s.z_max_pm for s in spans),
            )
        )
    return Extrusion3D(
        domain=Domain3D(
            lo=setup.lo,
            hi=setup.hi,
            z_min_pm=setup.z_low,
            z_max_pm=setup.z_high,
            boundaries=Boundaries3D(**{face.value: setup.faces[face] for face in Face}),
        ),
        materials=tuple(m for m in sorted(tech.materials, key=lambda m: m.name) if m.name in names),
        background=tech.stack.background,
        films=tuple(films),
        prisms=tuple(prisms),
        ports=tuple(ports),
    )


def _reduced(
    setup: _Setup, spec: EffectiveIndexSpec, wavelength_pm: int
) -> Result[tuple[Reduced2D, list[Approximation]]]:
    order = _painter_order(setup.layer_levels, setup.drawn)
    reduction = reduce_stack(
        setup.tech,
        order,
        Window(setup.z_low, setup.z_high),
        wavelength_pm,
        spec.polarization,
        spec.mode_order,
    )
    if reduction.value is None:
        return Result(None, reduction.diagnostics)
    columns = {c.layer: c.n_eff for c in reduction.value.columns}
    approximations: list[Approximation] = [reduction.value]
    patterned = {lv.name for name in order for lv in setup.layer_levels[name]}
    sloped = sorted(
        entry.level.name
        for entry in setup.levels
        if entry.level.name in patterned and _deg(entry.level) is not None
    )
    if sloped:
        approximations.append(SidewallAngleIgnored(levels=tuple(sloped)))
    description = Reduced2D(
        domain=Domain2D(
            lo=setup.lo,
            hi=setup.hi,
            boundaries=Boundaries2D(
                **{face.value: setup.faces[face] for face in Face if face.value[0] != "z"}
            ),
        ),
        background_index=columns[None],
        regions=tuple(
            Region2D(layer=name, polygons=setup.drawn[name], index=columns[name]) for name in order
        ),
        ports=tuple(setup.plane(path, port) for path, port in setup.ports),
    )
    return Result.of((description, approximations), reduction.diagnostics)
