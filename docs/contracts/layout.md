# Geometry and Layout IR

Contracts: `waffle/contracts/geometry/` (grid, transform, shapes, unsupported) and
`waffle/contracts/layout/` (the `Layout` record). Services: `waffle/geometry/services/` and
`waffle/layout/services/`. The IR is backend-neutral: no gdsfactory or klayout concepts.

## Coordinates and grid

* Every coordinate and length in the IR is a Python `int` in **database units (dbu)**.
  Floats are rejected on load.
* `Grid.dbu_pm` is the database unit in **integer picometres** (`1000` = 1 nm). Picometres
  make every supported length unit (m, mm, um, nm) and every practical dbu an exact integer.
  `Grid.manufacturing` is the manufacturing grid in dbu (default 1); points, port centres,
  and instance translations must be multiples of it (`layout.grid.off_manufacturing`).
* Physical to dbu (`geometry.services.grid.to_dbu`): the float is read as the decimal that
  prints for it (`0.1`, not `0.1000000000000000055`), then converted with exact rational
  arithmetic. Off-grid values are an error (`geometry.grid.off_grid`) unless a `Rounding`
  (`NEAREST` with ties to even, `FLOOR`, `CEIL`) is requested; snapping returns the exact
  error in picometres (`Snap.error_pm`, a `Fraction`) and a `geometry.grid.snapped` warning.
* dbu to physical (`from_dbu`, `dbu_to_float`): the correctly rounded float of the exact
  value, so on-grid values round-trip exactly (um to dbu to um returns the original float).
* Re-gridding (`layout.services.regrid`) rescales everything by the exact ratio of the two
  dbu. It is exact when the ratio divides every value; otherwise an error per cell, or
  snapping under an explicit `Rounding` with a warning stating the worst error.

## Transform

`Transform(mirror, rotation, translation)` maps a point as

```
apply(p) = R(rotation) * M^mirror * p + translation
```

`M` mirrors about the x axis (`y -> -y`) and is applied **first**, then a counter-clockwise
rotation (y up) by `rotation` in {0, 90, 180, 270}, then the integer translation (the GDS
STRANS order). All eight orientations are exact integer maps.

`outer.compose(inner)` is `outer` after `inner`: `outer.compose(inner).apply(p) ==
outer.apply(inner.apply(p))`. Composition is associative and not commutative. `inverse()`
is exact. Worked example:

```
outer = Transform(rotation=90, translation=(100, 0))
inner = Transform(mirror=True, translation=(10, 0))
inner.apply((1, 2))                 = (11, -2)       # mirror (1,-2), +(10,0)
outer.apply((11, -2))               = (102, 11)      # rotate (2,11), +(100,0)
outer.compose(inner)                = Transform(mirror=True, rotation=90, translation=(100, 10))
outer.compose(inner).apply((1, 2))  = (102, 11)
```

An instance's `transform` maps child frame to parent frame, so a point in a
nested cell maps to the top frame by `top_inst.compose(mid_inst).compose(leaf_inst).apply(p)`.
Arbitrary angles are **not** supported (see below): rounding them to the grid would make port
orientations and connectivity inexact. `Placement` (circuit level) converts with
`geometry.services.transforms.placement_to_transform`: `mirror` is the x-axis mirror above,
`rotation_deg` is counter-clockwise, and the origin must be on the grid.

## Shapes

* `Polygon`: simple, no holes. Normalized on construction: duplicate and collinear vertices
  removed, counter-clockwise, starting at the lexicographically smallest vertex, closing
  vertex omitted. Zero area or fewer than 3 vertices is rejected. Self-intersection is
  checked by `validate_layout` (quadratic; `check_simple=False` skips it).
* `Rectangle(lo, hi)` with `lo < hi` on both axes; `Rectangle.spanning(a, b)` sorts corners.
* `Path(points, width, end)`: centre line, full width, `end` of `flush` or `square`
  (extends half the width). Supported paths are Manhattan, have an even width, no repeated
  point and no reversal; corners are mitred. `path_to_polygon` gives the exact outline.
* Curves are **unsupported**. Exact arcs would need irrational coordinates; discretizing
  needs a tolerance policy that belongs to the producer. Adapters discretize upstream and
  may record the intent in `metadata`.

## Layout

`Layout` (record kind `layout`, id `layout:<name>`): `grid`, `top`, `layers`, `cells`.
`Cell`: `name`, `shapes` (logical layer name to shapes), `instances`, `ports`.
`Instance`: `name`, child `cell`, `transform`, and optional `array`. The transform
places the first member. An array stores row and column counts and their step vectors,
expressed in the parent frame in dbu. Active steps are signed Manhattan vectors, with
perpendicular directions when both axes repeat. A singleton axis has zero step.
Ordinary instances omit `array` from their serialized form and keep their original
canonical bytes and revision hashes.

* **Layers.** Shapes and ports reference a layer by its **logical name**. The layer table
  `LayerSpec(name, layer, datatype)` maps each name to its GDS pair; names and pairs are
  each unique.
* **Ports.** `Port(name, center, width, orientation, layer, domain)`; `domain` is the
  component `Domain` (optical or electrical). `orientation` is the direction the port faces
  (0 = +x, 90 = +y, counter-clockwise, in the cell's frame), pointing out of the component;
  mating optical ports face opposite ways (`(a - b) % 360 == 180`). Transforming a port
  moves `center` by the full transform and `orientation` by its linear part (mirror about
  x maps angle `a` to `-a`, then add the rotation); everything else is unchanged.
* **Identifiers.** Names match `[A-Za-z0-9_][A-Za-z0-9_.$-]*` (no `/`). Cell names are
  unique in a layout; instance names and port names are unique in a cell. A flattened port
  is identified by its **path**: `name` for the cell's own ports, `inst/name`,
  `outer/inner/name` for ports reached through instances. Top-level port names are never
  altered. Paths are unambiguous because names contain no `/`.
* **Validation** (`layout.services.validate.validate_layout`, one diagnostic per problem,
  located by names): `layout.layer.duplicate_name`, `layout.layer.duplicate_pair`,
  `layout.cell.duplicate`, `layout.instance.duplicate`, `layout.port.duplicate`,
  `layout.top.unknown`, `layout.instance.unknown_cell`, `layout.hierarchy.cycle`
  (message gives the cycle), `layout.shape.unknown_layer`, `layout.port.unknown_layer`,
  `layout.grid.off_manufacturing`, `geometry.polygon.self_intersecting`, and the path codes
  `geometry.path.odd_width`, `geometry.path.zero_length`, `geometry.path.reversal`.
  Structural errors (bad names, non-integers, rotation 45, unknown fields) fail
  `load_record` with `record.invalid` diagnostics located at the field.

## Serialization and ordering

Canonical JSON via `to_canonical_json`; the revision is the hash of the whole record
(`revision_of`), so any change including `metadata` changes it. Coordinates are
integer pairs (`"points":[[0,0],[4,0],[0,2]]`). Everything that is a set by meaning is
**stored sorted**, so the same layout always serializes identically regardless of
construction order: layers, cells, instances, ports by name; shapes within a layer by
`shape_key` (type, then geometry); layer keys of `shapes` by name. Duplicate shapes are
kept. Cell order is therefore not significant; the `top` field names the root.

## Services

| Module | Provides |
|---|---|
| `geometry.services.grid` | `to_dbu`, `from_dbu`, `dbu_to_float`, `Rounding`, `Snap` |
| `geometry.services.shapes` | `path_to_polygon`, `to_polygon`, `is_simple`, `validate_shape` |
| `geometry.services.transforms` | `manhattan_angle`, `transform_from_parts`, `placement_to_transform` |
| `layout.services.validate` | `validate_layout`, `find_cycles` |
| `layout.services.cells` | `transform_cell`, `instance_ports`, `QualifiedPort` |
| `layout.services.flatten` | `flatten` to `FlatCell` (sorted per-layer polygons, ports by path, `bounding_box`) |
| `layout.services.window` | `extract_window` to select whole polygons and qualified ports in a closed window |
| `layout.services.physical` | `to_physical` to `PhysicalCell`: float lengths in a unit (default `um`) |
| `layout.services.regrid` | `regrid` |
| `layout.services.compare` | `compare_layouts` in `HIERARCHICAL` or `FLAT` mode |

Array member port paths append zero-based row and column indices, for example
`block/array/2/5/in`. Transforming a cell moves the step vectors along with its
instances, so these indices continue to identify the corresponding member.

`extract_window` accepts a closed `BBox` in the selected cell's frame. It selects
polygons that touch the window and ports whose centers lie within it. It returns
whole outlines, including portions outside the window, so later physical
construction can use the original feature boundaries. It indexes the bounds of
unique referenced cells and computes the intersecting array index ranges using
integer arithmetic. Window selection is a layout operation. Physical extraction
also needs the process stack and any geometry outside the window that contributes
after extrusion.

The window test matches a fully expanded reference with rotated, mirrored, and
nested array placements. A second fixture selects exactly one member from a
compact 30,000 × 30,000 array. A local macOS ARM64 check of that single-window
query took about 0.05 ms with 9.8 KiB of peak traced Python allocations.

Comparison ignores ids, metadata, manufacturing grid, and storage order; shapes compare as
normalized polygons (a rectangle equals its four-corner polygon, a path its outline) as
multisets, without merging overlaps. Codes: `layout.diff.grid`, `.layer`, `.top`, `.cell`,
`.shapes`, `.instance`, `.port`. `FLAT` compares flattened polygons per layer and ports by
path, so cell names and hierarchy depth may differ but instance names (part of port paths)
may not.

## Unsupported constructs

The IR cannot express these. Adapters report them through
`geometry.unsupported.unsupported(Unsupported.X, detail, subject=..., location=...)`, which
yields an error `Diagnostic` with the stable code:

| Code | Construct |
|---|---|
| `geometry.unsupported.curve` | arcs, splines, bends (discretize upstream) |
| `geometry.unsupported.polygon_holes` | polygons with holes |
| `geometry.unsupported.shape` | any other shape type (text, ellipse, ...) |
| `geometry.unsupported.path_end` | path end styles other than flush and square |
| `geometry.unsupported.path_segment` | diagonal path segments |
| `geometry.unsupported.rotation` | angles that are not multiples of 90 degrees |
| `geometry.unsupported.magnification` | magnification other than 1 |
| `layout.unsupported.array_instance` | array references outside the supported repetition or adapter subset |
| `layout.unsupported.external_reference` | instance of a cell outside the layout |

`transform_from_parts` and `placement_to_transform` raise the rotation and magnification
codes themselves; `validate_layout` raises `path_segment` for diagonal paths.
