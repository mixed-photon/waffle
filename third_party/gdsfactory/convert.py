"""klayout layouts and gdsfactory ports to Layout IR.

`layout_from_klayout` is the single conversion used for generated components, imported GDS,
and the output of geometry operations. Every cell of the klayout layout is converted;
`top` only names the layout's top cell. Whatever the IR cannot express is reported, never
dropped, with the shared codes of `waffle.contracts.geometry.unsupported` and `BackendCode`:

* array instances, instances that are rotated by other than a multiple of 90 degrees or
  magnified (`ICplxTrans` angles within 1e-6 degrees of a multiple of 90 and magnifications
  within 1e-9 of 1 are accepted as exact), polygons with holes (including the zero-width
  keyholes GDS uses for them), round paths and paths with other end styles, text and other
  non-polygon shapes, geometry on a GDS layer the layer table lacks, degenerate shapes, and
  ports that are off the grid, not at a multiple of 90 degrees, unnamed, or neither optical
  nor electrical;
* names that do not match the IR `Name` pattern are rewritten deterministically (illegal
  characters become `_`, a leading `-`, `.`, or `$` gets a `_` prefix); each rewrite is an
  info diagnostic and the original is kept in `Layout.metadata` as `name.<kind>.<new>`; two
  names that clean to the same text are an error;
* GDS instances have no names unless the file carries kfactory's instance property; unnamed
  ones are called `<child>_<n>` in a deterministic order, with an info diagnostic.

A port's mirror flag is ignored (the IR has no mirrored ports); only its position and
direction are used.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

import kfactory as kf
import klayout.db as kdb
from pydantic import ValidationError

from third_party.gdsfactory.gds import INSTANCE_NAME_KEY
from waffle.contracts.component.definition import Domain
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.result import Result
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Path, Polygon, Rectangle
from waffle.contracts.geometry.unsupported import Unsupported, unsupported
from waffle.contracts.layout.layout import Cell, Instance, LayerSpec, Layout, Port
from waffle.geometry.services.transforms import manhattan_angle, transform_from_parts
from waffle.layout.backend.interface import BackendCode, diagnostic
from waffle.layout.services.validate import validate_layout

ANGLE_TOLERANCE_DEG = 1e-6
MAGNIFICATION_TOLERANCE = 1e-9
_ILLEGAL = re.compile(r"[^A-Za-z0-9_.$\-]")
_DOMAINS = {"optical": Domain.OPTICAL, "electrical": Domain.ELECTRICAL}
Location = tuple[str | int, ...]


def clean_name(original: str) -> str:
    """`original` as an IR `Name`: illegal characters become `_`, and a leading `-`, `.`,
    `$` (or an empty name) gets a `_` prefix."""
    cleaned = _ILLEGAL.sub("_", original)
    return cleaned if cleaned[:1] not in ("", "-", ".", "$") else "_" + cleaned


def _snap_angle(angle: float) -> float:
    nearest = round(angle / 90) * 90
    return float(nearest) if abs(angle - nearest) <= ANGLE_TOLERANCE_DEG else angle


class _Converter:
    def __init__(
        self,
        ly: kdb.Layout,
        layout_id: EntityId,
        layers: Sequence[LayerSpec],
        ports: Mapping[str, Sequence[Port]],
    ) -> None:
        self.ly = ly
        self.subject = str(layout_id)
        self.pairs = {(spec.layer, spec.datatype): spec.name for spec in layers}
        self.ports = ports
        self.found: list[Diagnostic] = []
        self.metadata: dict[str, str] = {}
        self._grouped: Counter[Diagnostic] = Counter()
        self.cell_names: dict[str, str] = {}

    def report(self, finding: Diagnostic) -> None:
        """Record a finding, folding repeats (one per layer and kind) into a count."""
        self._grouped[finding] += 1

    def flush(self) -> None:
        for finding, count in self._grouped.items():
            note = f" ({count} times)" if count > 1 else ""
            self.found.append(finding.model_copy(update={"message": finding.message + note}))

    def name(self, kind: str, original: str, scope: str = "") -> str:
        cleaned = clean_name(original)
        if cleaned != original:
            self.metadata[f"name.{kind}.{scope}{cleaned}"] = original
            self.found.append(
                diagnostic(
                    BackendCode.NAME_SANITIZED,
                    f"{kind} name {original!r} became {cleaned!r}",
                    severity=Severity.INFO,
                    subject=self.subject,
                )
            )
        return cleaned

    def shapes(self, cell: kdb.Cell, name: str) -> dict[str, list[Polygon | Rectangle | Path]]:
        out: dict[str, list[Polygon | Rectangle | Path]] = {}
        for index in self.ly.layer_indexes():
            info = self.ly.get_info(index)
            shapes = cell.shapes(index)
            if shapes.is_empty():
                continue
            pair = (info.layer, info.datatype)
            layer = self.pairs.get(pair)
            where: Location = ("cells", name, "shapes", f"{info.layer}/{info.datatype}")
            if layer is None:
                self.report(
                    diagnostic(
                        BackendCode.LAYER_UNMAPPED,
                        f"GDS layer {pair[0]}/{pair[1]} is not in the layer table "
                        f"({shapes.size()} shapes)",
                        subject=self.subject,
                        location=where,
                    )
                )
                continue
            for shape in shapes.each():
                converted = self._shape(shape, where)
                if converted is not None:
                    out.setdefault(layer, []).append(converted)
        return out

    def _shape(self, shape: kdb.Shape, where: Location) -> Polygon | Rectangle | Path | None:
        def bad(construct: Unsupported, detail: str) -> None:
            self.report(unsupported(construct, detail, subject=self.subject, location=where))

        try:
            if shape.is_box():
                box = shape.box
                return Rectangle(lo=(box.left, box.bottom), hi=(box.right, box.top))
            if shape.is_path():
                path = shape.path
                if path.round:
                    bad(Unsupported.CURVE, "round path")
                    return None
                half = path.width // 2
                ends = (path.bgn_ext, path.end_ext)
                if ends not in ((0, 0), (half, half)):
                    bad(Unsupported.PATH_END, f"path extensions {ends} for width {path.width}")
                    return None
                points = tuple((p.x, p.y) for p in path.each_point())
                return Path(
                    points=points, width=path.width, end="flush" if ends == (0, 0) else "square"
                )
            if shape.is_polygon() or shape.is_simple_polygon():
                polygon = shape.polygon
                ring = tuple((p.x, p.y) for p in polygon.each_point_hull())
                keyhole = len(set(ring)) != len(ring) and any(
                    q.holes() for q in kdb.Region(polygon).merged().each()
                )
                if polygon.holes() or keyhole:
                    bad(Unsupported.POLYGON_HOLES, "polygon with a hole")
                    return None
                return Polygon(points=ring)
        except ValidationError as exc:
            self.report(
                diagnostic(
                    BackendCode.DEGENERATE_SHAPE,
                    exc.errors()[0]["msg"],
                    subject=self.subject,
                    location=where,
                )
            )
            return None
        kind = "text" if shape.is_text() else "non-polygon"
        bad(Unsupported.SHAPE, kind)
        return None

    def instances(self, cell: kdb.Cell, name: str) -> list[Instance]:
        placed: list[tuple[str, str | None, object]] = []  # (child, explicit name, transform)
        for index, inst in enumerate(cell.each_inst()):
            where: Location = ("cells", name, "instances", index)
            child = self.cell_names[inst.cell.name]
            if inst.is_regular_array():
                self.report(
                    unsupported(
                        Unsupported.ARRAY_INSTANCE,
                        f"{inst.na}x{inst.nb} array of {child!r}",
                        subject=self.subject,
                        location=where,
                    )
                )
                continue
            ct = inst.cplx_trans
            mag = 1.0 if abs(ct.mag - 1.0) <= MAGNIFICATION_TOLERANCE else ct.mag
            transform = transform_from_parts(
                (ct.disp.x, ct.disp.y),
                _snap_angle(ct.angle),
                ct.is_mirror(),
                mag,
                subject=self.subject,
                location=where,
            )
            if transform.value is None:
                for finding in transform.diagnostics:
                    self.report(finding)
                continue
            explicit = inst.property(INSTANCE_NAME_KEY)
            placed.append((child, explicit if isinstance(explicit, str) else None, transform.value))
        return self._named(name, placed)

    def _named(self, cell: str, placed: list[tuple[str, str | None, object]]) -> list[Instance]:
        used: set[str] = set()
        out: list[Instance] = []
        unnamed = []
        for child, explicit, transform in placed:
            if explicit is None:
                unnamed.append((child, transform))
                continue
            cleaned = self.name("instance", explicit, f"{cell}.")
            if cleaned in used:
                self.found.append(
                    diagnostic(
                        BackendCode.NAME_COLLISION,
                        f"two instances of cell {cell!r} are called {cleaned!r}",
                        subject=self.subject,
                        location=("cells", cell, "instances"),
                    )
                )
            used.add(cleaned)
            out.append(Instance(name=cleaned, cell=child, transform=transform))
        if unnamed:
            self.found.append(
                diagnostic(
                    BackendCode.INSTANCE_NAMES_GENERATED,
                    f"{len(unnamed)} instances in cell {cell!r} have no name",
                    severity=Severity.INFO,
                    subject=self.subject,
                    location=("cells", cell, "instances"),
                )
            )
        counters: Counter[str] = Counter()
        key = lambda item: (item[0], item[1].mirror, item[1].rotation, item[1].translation)  # noqa: E731
        for child, transform in sorted(unnamed, key=key):
            counters[child] += 1
            while (candidate := f"{child}_{counters[child]}") in used:
                counters[child] += 1
            used.add(candidate)
            out.append(Instance(name=candidate, cell=child, transform=transform))
        return out


def layout_from_klayout(
    ly: kdb.Layout,
    *,
    layout_id: EntityId,
    grid: Grid,
    layers: Sequence[LayerSpec],
    top: str | None,
    ports: Mapping[str, Sequence[Port]],
    metadata: Mapping[str, str] | None = None,
) -> Result[Layout]:
    """The IR of every cell in `ly`, or the diagnostics saying what cannot be expressed.

    `grid` must match the klayout database unit; `ports` maps klayout cell names to their
    ports (klayout has none); `top` is the top cell's klayout name, defaulting to the only
    cell no other cell references. The result passes `validate_layout`.
    """
    converter = _Converter(ly, layout_id, layers, ports)
    subject = converter.subject
    if abs(ly.dbu * 1e6 - grid.dbu_pm) > 1e-6:
        return Result(
            None,
            (
                diagnostic(
                    BackendCode.GRID_MISMATCH,
                    f"upstream database unit is {ly.dbu * 1e6:g} pm, expected {grid.dbu_pm} pm",
                    subject=subject,
                ),
            ),
        )
    tops = [cell.name for cell in ly.top_cells()]
    chosen = top if top is not None else (tops[0] if len(tops) == 1 else None)
    if chosen is None or not any(cell.name == chosen for cell in ly.each_cell()):
        message = (
            f"top cell {top!r} is not in the layout"
            if top is not None
            else f"expected one top cell, found {tops or 'none'}"
        )
        code = "layout.top.unknown" if top is not None else BackendCode.TOP_AMBIGUOUS
        return Result(None, (diagnostic(code, message, subject=subject),))
    originals = sorted(cell.name for cell in ly.each_cell())
    for original in originals:
        cleaned = converter.name("cell", original)
        if cleaned in converter.cell_names.values():
            converter.found.append(
                diagnostic(
                    BackendCode.NAME_COLLISION,
                    f"cell names collide after cleaning: {original!r} -> {cleaned!r}",
                    subject=subject,
                )
            )
        converter.cell_names[original] = cleaned
    cells: list[Cell] = []
    for original in originals:
        name = converter.cell_names[original]
        cell = ly.cell(original)
        cells.append(
            Cell(
                name=name,
                shapes={k: tuple(v) for k, v in converter.shapes(cell, name).items()},
                instances=tuple(converter.instances(cell, name)),
                ports=tuple(ports.get(original, ())),
            )
        )
    converter.flush()
    if any(d.severity is Severity.ERROR for d in converter.found):
        return Result(None, tuple(converter.found))
    layout = Layout(
        id=layout_id,
        grid=grid,
        top=converter.cell_names[chosen],
        layers=tuple(layers),
        cells=tuple(cells),
        metadata={**(metadata or {}), **converter.metadata},
    )
    return Result.of(layout, [*converter.found, *validate_layout(layout)])


def kfactory_ports(
    kcl: kf.KCLayout, layers: Sequence[LayerSpec], *, subject: str
) -> Result[dict[str, tuple[Port, ...]]]:
    """The ports of every cell of `kcl` as IR ports, keyed by cell name."""
    names = {(spec.layer, spec.datatype): spec.name for spec in layers}
    dbu_um = kcl.dbu
    found: list[Diagnostic] = []
    result: dict[str, tuple[Port, ...]] = {}

    for cell in kcl.kcells.values():
        converted: list[Port] = []
        for index, port in enumerate(cell.ports):
            where: Location = ("cells", cell.name, "ports", index)

            def refuse(detail: str, where: Location = where) -> None:
                found.append(
                    diagnostic(
                        BackendCode.PORT_UNSUPPORTED, detail, subject=subject, location=where
                    )
                )

            if not port.name:
                refuse("port has no name")
                continue
            if (domain := _DOMAINS.get(port.port_type)) is None:
                refuse(f"port {port.name!r} has type {port.port_type!r}")
                continue
            info = kcl.layout.get_info(port.layer)
            if (layer := names.get((info.layer, info.datatype))) is None:
                refuse(f"port {port.name!r} is on GDS layer {info.layer}/{info.datatype}")
                continue
            trans = port.base.trans
            if trans is not None:
                centre, degrees = (trans.disp.x, trans.disp.y), trans.angle * 90.0
            else:
                complex_trans = port.base.dcplx_trans
                x, y = complex_trans.disp.x / dbu_um, complex_trans.disp.y / dbu_um
                if abs(x - round(x)) > 1e-6 or abs(y - round(y)) > 1e-6:
                    refuse(f"port {port.name!r} is off the grid at ({x:g}, {y:g}) dbu")
                    continue
                centre, degrees = (round(x), round(y)), _snap_angle(complex_trans.angle)
            orientation = manhattan_angle(degrees, subject=subject, location=where)
            if orientation.value is None:
                found.extend(orientation.diagnostics)
                continue
            converted.append(
                Port(
                    name=clean_name(port.name),
                    center=centre,
                    width=port.width,
                    orientation=orientation.value,
                    layer=layer,
                    domain=domain,
                )
            )
        if converted:
            result[cell.name] = tuple(converted)
    return Result.of(result, found)
