# Technology, process stack, materials, and PDKs

Records are in `waffle/contracts/technology/`; consumption and resolution services in
`waffle/technology/`; PDK authoring, validation, and export in `waffle/pdk/`. A synthetic
fixture PDK for tests is `//waffle/pdk/testing:synthetic` (marked synthetic in its metadata;
not foundry data; importable from any layer's tests).

## Records

| Record | Kind | Holds |
|---|---|---|
| `TechnologyRevision` | `technology` | `dbu_pm` and optional `manufacturing_grid_pm` (exact integer picometres), `optical_band`, layer table, process stack, materials |
| `PdkManifest` | `pdk` | PDK `version`, compatible Waffle specifier, exact dependency pins, `access`, `license`, pinned technology revisions |
| `Pdk` | (container) | a manifest and the technology revisions it ships |

`TechnologyRef` (`technology:<name>@sha256:...`) pins one revision of a `TechnologyRevision`
exactly like any other record. Edits to anything, descriptions included, give a new revision.

**Layer table.** Each `LayoutLayer` has a logical `name`, a GDS `(gds_layer, gds_datatype)`
pair, a `purpose` (`drawing` defines shapes; `annotation` such as ports or device recognition
never patterns the stack), and a `description`.

## Process stack conventions

* z points up from the substrate. Each `ProcessLevel` has `z_min` and `thickness` (length
  quantities, any length unit; compared in SI). Levels are listed bottom to top; they may
  touch but not overlap (tolerance 1 pm).
* `Blanket`: unpatterned, `material` fills the whole plane over the level's z range
  (substrate, buried oxide, top cladding).
* `Patterned(layers, fill)`: `material` where any listed layer is drawn (union), `fill`
  elsewhere. A partial etch is stacked levels: a 90 nm `slab` level patterned by `WG` and
  `SLAB` under a 130 nm `core` level patterned by `WG` (see the synthetic fixture).
* z intervals inside the stack not covered by a level, and everything above and below the
  stack, hold `stack.background` (the simulation background/cladding).
* The drawn outline is the footprint at the level's bottom; `sidewall_angle` is from the
  substrate plane, 90 deg or `None` is vertical.
* Nothing in the records assumes 2D. `waffle.technology.stack.vertical_column(tech, present)`
  returns the exact material column `[(z_min_m, z_max_m, material)]` where exactly the layers
  in `present` are drawn (inside versus outside a waveguide: `["WG"]` versus `[]`). A
  layout-to-physics step builds a 3D extrusion from the levels, or declares an effective-index
  reduction from these columns plus `refractive_index`; it must record which. Waffle ships no
  mode solver here.

## Materials

`Material(name, optical, validity, source)`; `optical` is one of `ConstantIndex(n, k)`,
`Sellmeier(b, c_um2)` (`n^2 = 1 + sum b L^2 / (L^2 - c)`, `L` in um), or
`Tabulated(wavelength_um, n, k, interpolation="linear")`. Index is `n + i*k`, `k >= 0` is loss.
`validity` is a wavelength range; `source` is a citation. `refractive_index(material,
wavelength)` returns a `Result[complex]` and never extrapolates: outside the validity range
(or the table) it gives `technology.material_out_of_range`. Electrical and thermal properties
are not modelled; they will be sibling fields of `optical` in a later schema version.

## Validation and queries

`validate_technology(tech)` returns diagnostics with codes `technology.*` (listed in the
module docstring of `waffle/technology/validate.py`): grid mismatch, duplicate layer names,
GDS pairs, materials, levels, unknown materials and layers, annotation layers patterning the
stack, non-positive thickness, sidewall errors, overlapping or misordered levels, material
validity not covering `optical_band` (for materials the stack uses), Sellmeier poles inside
the validity range, and unit problems (`units.*`). An unpatterned drawing layer is a warning.

`map_layer(tech, name)` and `map_gds(tech, layer, datatype)` return the stack levels a layer
patterns, or `technology.layer_unknown` (missing), `technology.layer_ambiguous` (several
table entries match), `technology.layer_unmapped` (drawing layer patterning nothing).
`check_requirement(requirement, tech)` checks an implementation's `TechnologyRequirement`
(`technology.requirement_id_mismatch`, `technology.requirement_unknown_layer`);
`resolve_design(..., technology_record=tech)` applies it and also requires the record to be
the pinned revision (`design.technology_record_mismatch`). Omit the argument for the old
behaviour.

## Physical fingerprint

`physical_fingerprint(tech)` hashes only what a simulation or mapping depends on: `dbu_pm`,
`manufacturing_grid_pm`, `optical_band`; layer `name`, GDS pair, `purpose` (any order);
material `name`, optical model numbers, `validity` (any order); `stack.background` and each
level's `name`, z range, `material`, pattern kind, layers (any order) and `fill`,
`sidewall_angle`. Lengths and angles are SI with 12 significant digits, so `0.22 um` equals
`220 nm`. Excluded: technology `id`, `metadata`, `schema_version`, `LayoutLayer.description`,
`Material.source`. Revision digest changes on any edit; the fingerprint changes only on
physical edits. MOD-018 may derive narrower fingerprints (for example per layer).

## PDK package interface

A PDK is a `Pdk` (manifest plus technologies). Manifest fields: `id` (`pdk:<name>`),
`version` (dotted integers), `waffle` (compatible Waffle versions), `dependencies` (exact
`name`/`version` pins), `access` (`public` or `restricted`), `license`, `technologies`
(revisions it ships, which must equal the revisions provided).

**Version compatibility.** `waffle` is comma-separated clauses with `>=`, `>`, `<=`, `<`,
`==`, `!=`, such as `>=0.1,<0.2`; versions are dotted integers (`waffle.__version__`). Not
supported, and rejected rather than guessed: pre-releases, local versions, wildcards, `~=`.
This avoids a runtime dependency on `packaging`.

**Entry points.** An installed PDK package declares, in its `pyproject.toml`:

```toml
[project.entry-points."waffle.pdks"]
demo_soi = "waffle_pdk_demo:demo_pdk"      # a zero-argument callable returning a Pdk
```

`PdkRegistry.discover()` loads every entry point of group `waffle.pdks`.

**Directory.** For local or restricted PDKs that are not installed:

```
<dir>/pdk.json                            manifest, canonical JSON
<dir>/technologies/<name>.<hex12>.json    one technology revision per file
```

`write_pdk(pdk, dir)` writes it (refusing invalid PDKs and existing ones); `read_pdk(dir)`
reads it back to an identical `Pdk`; `validate_pdk_directory(dir)` is the entry for a CLI
validate command. `PdkRegistry.add_directory(dir)` loads it.

**One path.** Both routes end in `PdkRegistry.add`, which runs `validate_pdk` (manifest versus
shipped technologies, each technology, Waffle compatibility) and registers only error-free
PDKs. `registry.resolve(ref)` returns the exact pinned revision, or
`technology.revision_unavailable` listing the loaded revisions of that technology (for
example after a PDK update), or `technology.unknown`. Two different PDKs with one id are
`pdk.duplicate`.

**Restricted PDKs.** A restricted PDK is the same package or directory with
`access = "restricted"`, distributed through its owner's channel (private index or private
directory). Waffle treats it identically; `access` is a recorded classification and Waffle
does not enforce it (SCALE-003). Core tests use only the synthetic fixture.
