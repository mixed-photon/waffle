# Layout backends

A layout backend generates, imports, exports, or transforms `Layout` IR with an upstream
engine. The interface is core and backend neutral (`waffle/layout/backend/`); adapters live
under `third_party/` (`third_party/gdsfactory/` is the first). GDS files are tied to their
metadata by a sidecar (`waffle/layout/formats/`). The IR is in `docs/contracts/layout.md`.

## Capabilities

A backend is any object with `info() -> BackendInfo`. Four small protocols, each separate:

| Capability | Protocol | Method |
|---|---|---|
| generation | `LayoutGenerator` | `generate(GenerationRequest) -> Result[Layout]` |
| import | `LayoutImporter` | `import_gds(bytes, ImportRequest) -> Result[Layout]` |
| export | `LayoutExporter` | `export_gds(Layout) -> Result[bytes]` |
| geometry | `GeometryOperations` | `merge(Layout)`, `boolean(Layout, BooleanOperation)` |

`BackendInfo` holds the `producer` (name, version, exact upstream library versions), the
`generators` offered (name, exact version, `DesignParameterSchema`, `example` values),
`import_formats`, `export_formats`, and `geometry_operations`. Its `capabilities` follow from
what is listed. A backend implements any subset. Callers ask with `generator_of`,
`importer_of`, `exporter_of`, `geometry_of`, which return a `Result` holding the backend, or
a `layout.backend.capability_unsupported` diagnostic (also when a capability is declared but
the method is missing); there is no `AttributeError` path.

Requests use only contracts. `GenerationRequest(generator: GeneratorSource, values:
DesignValues, technology: TechnologyRevision, layout_id)`; `request_for_design(design,
implementation, technology)` builds one from a `ResolvedDesign` after checking that both
records are exactly the revisions the design pins (the layout is named `layout:<design>`).
`ImportRequest(sidecar | (layers, layout_id), top)`: with a sidecar the bytes are verified
against it and it supplies ports, logical layer names, and the layout id. `export_package(
exporter, layout, design=, technology=, implementation=)` returns GDS bytes plus the sidecar.

Shared diagnostic codes (`BackendCode`): `capability_unsupported`, `generator_unknown`,
`request_invalid`, `layer_missing`, `layer_unmapped`, `degenerate_shape`, `grid_mismatch`,
`name_collision`, `name_sanitized` (info), `instance_names_generated` (info), `top_ambiguous`,
`port_unsupported`, `no_ports` (info), `import_failed`, `export_failed`, all under
`layout.backend.`. Constructs the IR cannot hold use the codes in
`waffle/contracts/geometry/unsupported.py`.

## Adding a backend

1. Implement `info()` and the protocols you support in `third_party/<name>/`. Use
   `waffle.layout.formats.sidecar` through `export_package`; never import the adapter from core.
2. Declare each generator with a Waffle-owned name (`waffle.<backend>.<generator>`), an exact
   version that changes with its geometry, and example values.
3. Run the shared cases (`waffle/layout/backend/conformance.py`) against the real library:

   ```python
   @pytest.mark.parametrize("case", applicable_cases(MyBackend()), ids=lambda c: c.name)
   def test_conformance(case: Case) -> None:
       case.run(MyBackend(), synthetic_soi())
   ```

   `applicable_cases` keeps the cases whose capabilities the backend declares. They cover
   capability consistency; generated layouts (valid, on the technology grid and layers,
   ports present, deterministic); rejection of unknown generators, extra, missing, and wrong
   dimension parameters; GDS round trip equal in hierarchical and flat modes (shared 3-level
   fixture and every generator); import without a sidecar (no ports, info diagnostic);
   modified bytes refused; merge and the four Boolean operations on a shared fixture; and
   holes reported as `geometry.unsupported.polygon_holes`. A native backend (NAT-001) runs
   the same ones. `//waffle/layout/backend:fake` runs them in the fast suite.
4. Tests of an upstream-backed adapter are tagged `backend` and `manual` so `bazel test //...`
   never fetches the upstream; collect them in one `test_suite`.

## GDS sidecar

`<name>.gds` is described by `<name>.gds.waffle.json`, a `gds_sidecar` record (canonical JSON,
id `gds_sidecar:<layout name>`, schema version 1):

| Field | Meaning |
|---|---|
| `sha256`, `size` | digest (`sha256:<hex>`) and byte count of the exact GDS file |
| `layout` | revision of the `Layout` exported |
| `grid`, `top`, `layers` | database unit and manufacturing grid, top cell, layer table (logical name and GDS pair) |
| `ports` | ports of every cell with ports (hierarchical comparison needs the subcells' too) |
| `producer` | backend name, version, upstream versions |
| `design`, `technology`, `implementation` | optional pinned revisions |
| `metadata` | free text |

`verify_sidecar(gds, sidecar)` returns diagnostics and needs no upstream library
(`gds_stream.summarize` reads record headers): `gds.sidecar.size_mismatch` and
`hash_mismatch` (modified file or wrong sidecar; checked first), then for matching bytes
`top_mismatch` (the sidecar's top is not a top cell of the file), `grid_mismatch`,
`layer_mismatch` (a pair in use is not in the layer table), `cell_mismatch`, and
`gds.stream.invalid`. `write_package` / `read_package` handle the file pair (`gds.file.missing`,
`gds.sidecar.missing`). `waffle.layout.formats.publish.publish_package(store, package)`
stores both files as one `layout_gds` artifact; provenance records operation
`layout.export_gds` run by the producer, with the layout, design, technology, and
implementation revisions as inputs; `load_package` reads and verifies it.

## The gdsfactory adapter

Runtime: the pinned `@pip_gdsfactory` hub (gdsfactory 9.51.0, kfactory 3.0.4, klayout 0.30.13),
reached through `//third_party/gdsfactory:upstream`. All adapter targets are `manual`.

```sh
bazel test //third_party/gdsfactory:all_backend_tests   # smoke, backend, convert, session
bazel test //third_party/gdsfactory:backend_test        # acceptance + shared conformance cases
```

Tests run Bazel's core and backend hubs side by side. Both put their own copy of pydantic,
pydantic-core, numpy, and typing-extensions on `sys.path`; the versions are pinned equal
(`session_test` asserts it) and the backend hub's copy is the one imported. Importing
`waffle.layout.backend` never imports gdsfactory (`session_test` checks in a fresh
interpreter). Adapter modules: `backend.py` (`GdsfactoryBackend`), `generators.py`,
`convert.py` (klayout and ports to IR), `gds.py` (IR to klayout, bytes), `geometry.py`,
`session.py`.

### Generators

Waffle names at version `1.0.0`; layer `WG` of the technology's layer table (GDS pair from
the table), ports on `WG`. Parameters are lengths in any length unit, converted exactly to
the technology's database unit (off-grid is an error), each at least 1 nm.

| Generator | Parameters (default) | Ports |
|---|---|---|
| `waffle.gdsfactory.straight` | `length` (required), `width` (0.5 um) | `o1` at (0, 0) facing 180, `o2` at (length, 0) facing 0 |
| `waffle.gdsfactory.mmi1x2` | `width` 0.5, `width_taper` 1.0, `length_taper` 10, `length_mmi` 5.5, `width_mmi` 2.5, `gap_mmi` 0.25 (um) | `o1` (-length_taper, 0) facing 180; `o2` (length_mmi + length_taper, +a), `o3` (same x, -a) facing 0, `a = (gap_mmi + width_taper) / 2` |
| `waffle.gdsfactory.mmi2x2` | same | `o1` (-length_taper, -a), `o2` (-length_taper, +a) facing 180; `o3` (..., +a), `o4` (..., -a) facing 0 |

The MMIs are hierarchical: top cell `mmi1x2` (body rectangle from (0, -width_mmi/2) to
(length_mmi, width_mmi/2); instances `in1`, `out1`, `out2`) and one `mmi1x2_taper` cell
(trapezoid from `width` to `width_taper`). Inputs are unrotated, outputs rotated 180 degrees.
Ports, extents, and orientations agree with gdsfactory's own `straight`, `mmi1x2`, `mmi2x2`
(compared against the generic PDK in `session_test`). Constraints, reported as
`layout.backend.request_invalid`: `width`, `width_taper`, `width_mmi` even in dbu,
`gap_mmi + width_taper` even, output tapers inside the body. Results are also checked by
`validate_layout`, which enforces the technology's manufacturing grid. The layout id defaults
to `layout:<generator>_<12 hex of a hash of the generator, dbu values, technology revision>`;
metadata records generator, version, technology revision, backend, and backend version.

### Convention mappings (each checked against the upstream code)

| Topic | Upstream | IR |
|---|---|---|
| Database unit | `Layout.dbu` in um | `Grid.dbu_pm = dbu * 1e6`, must equal the declared grid (`grid_mismatch`); technology `dbu_pm` and `manufacturing_grid_pm / dbu_pm` give a generated layout's grid |
| Instance transform | `Trans(rot, mirror, x, y)`, `ICplxTrans` | identical to `Transform` (mirror about x, then rotate counter-clockwise, then move); angles within 1e-6 degrees of a multiple of 90 and magnification within 1e-9 of 1 are exact; the integer displacement is what klayout stores |
| Port | `DPort` centre in um, orientation in degrees, counter-clockwise from +x pointing out of the component | same convention; centre in dbu; the mirror flag is ignored; `trans` is integer, otherwise `dcplx_trans` must lie on the grid |
| Port domain | `port_type` `optical` or `electrical` | `Domain`; other types are `port_unsupported` |
| Layer | `(layer, datatype)` | logical name via the layer table; a pair with shapes but no entry is `layer_unmapped` |
| Shapes | box, polygon, path | `Rectangle`, `Polygon`, `Path` (flush or square end); paths export as outline polygons |
| Holes | polygons with holes; GDS stores them as zero-width keyholes | `polygon_holes` (both forms detected) |
| Instance name | property key 0 (kfactory `PROPID.NAME`), written the same way on export | `Instance.name`; absent names become `<child>_<n>` in a deterministic order, info diagnostic |
| Instance array | one compact GDS AREF with column/row counts and exact integer spacing | `Instance.array`, with columns along the transformed child-local +X axis and rows along the transformed child-local +Y axis |
| Cell, instance, port names | any string | IR `Name`: illegal characters become `_`, a leading `-`, `.`, `$` gets `_`; rewrite is an info diagnostic and `Layout.metadata["name.<kind>.<new>"]` keeps the original; clashes are `name_collision` |
| Cell names | `gf.Component(name=...)` keeps the given name (unnamed ones get gdsfactory's generated names) | taken as is, then normalised as above |
| Cells | all cells of the file | all cells are imported; `top` is the sidecar's or the request's, else the only unreferenced cell (`top_ambiguous`) |
| GDS bytes | klayout writes timestamps | not written (`gds2_write_timestamps`), so equal layouts give equal bytes, also across processes |

### Global state

gdsfactory 9.51.0 keeps the active PDK, a default layout (`kfactory.kcl`) that caches every
cell, and registries of layouts and klayout libraries. `Pdk.activate()` rewrites the default
layout's layers and database unit and refuses a unit change once it holds cells. Adapter
calls therefore run in `session.session(dbu_pm)`: a private `KCLayout` with the technology's
unit, a throwaway PDK assigned to `gdsfactory.pdk._ACTIVE_PDK` (needed because
`add_polygon` and `add_port` call `get_active_pdk` even for integer layer indexes), and full
restoration and deletion on exit, including on exceptions. Layers are integer indexes of the
private layout, so gdsfactory's generic layers never appear. Sessions are serialized by a
lock; a PDK activated concurrently from another thread is not protected. A user's own active
PDK is neither used nor changed (`session_test`).

### Geometry operations

`merge` unions overlapping and edge-abutting shapes per layer in every cell; `boolean`
(`and`, `or`, `not` = a minus b, `xor`) sets `out` from `a` and `b` in every cell. Both work
on each cell's own shapes (hierarchy and ports kept) with klayout regions; results are merged
polygons. A result with a hole (for example `b` strictly inside `a` with `not`) is
`geometry.unsupported.polygon_holes`.

### Instance arrays

Regular rectangular GDS arrays stay compact. Import canonicalizes reversed or transposed
foreign array indices with `layout.backend.array_normalized`, preserving the placement
coordinates and member transforms. A 1 × 1 reference becomes an ordinary instance.
Export requires Waffle array axes to follow the child-local +X and +Y order so the
instance origin and indexed port paths survive a round trip. Invalid or unsupported
arrays report `layout.unsupported.array_instance`. This includes off-grid spacing, zero
or nonorthogonal active steps, and counts above 32,767. AREF endpoint coordinates must
fit signed 32-bit integers and the span from the origin to either endpoint must have
magnitude at most 2³¹ − 1 dbu.

### Known unsupported constructs

Rotations other than multiples of 90 degrees and magnification other than 1 on instances
or ports; curves (round paths); paths
with other end extensions; diagonal path segments (`validate_layout`); polygons with holes;
text and other non-polygon shapes; instances of cells the file does not define
(`layout.unsupported.external_reference`); non-optical, non-electrical, unnamed, or off-grid
ports; polygons of more than 8000 vertices on export (klayout would split them,
`layout.backend.export_failed`). A hand-written GDS has no ports unless a sidecar supplies
them. Not covered: OASIS, cell properties, labels, layer purposes beyond the GDS pair,
multiple dbu per file, PCells and library proxies.
