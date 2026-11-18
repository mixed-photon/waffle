# Physical problems: layout and technology to physics

Contracts: `waffle/contracts/physics/` (`problem`, `approximations`). Preparation:
`waffle/simulation/common/` (`prepare_physical_problem`, `request`, `ports`, `fingerprint`).
Effective-index reduction: `waffle/simulation/photonics/device_analysis/` (`slab`, `reduction`).
The analysis request (excitations, observables, numerical settings) is MOD-011's; this layer
stops at "what is simulated, where, with which boundaries and ports". Nothing here is
solver-specific: no grid, mesh, time step, or backend name.

## Records

`PhysicalProblem` (kind `physical_problem`, id `physical_problem:<name>`):

| Field | Meaning |
|---|---|
| `layout`, `top_cell` | exact `RevisionRef` of the source `Layout` and the flattened cell |
| `technology`, `design` | exact `TechnologyRef`; optional resolved-design ref (as given in the request) |
| `dbu_pm` | picometres per in-plane integer unit (the layout's) |
| `description` | `Extrusion3D` or `Reduced2D`, tagged by `dimensionality` (`"3d"`/`"2d"`) |
| `approximations` | typed list; empty for a 3D problem |

**Coordinates.** x, y are the layout axes in integer dbu; z (up from the substrate) and every
thickness are integer picometres; so are wavelengths. Floats appear only as indices and
sidewall angles (12 significant digits), so serialization and the revision are deterministic.
`PhysicalProblem.metres(dbu)` converts.

**3D (`Extrusion3D`).** Paint order: `background`, then `films` bottom to top (a blanket level, or
the `fill` of a patterned level, over the whole plane, cut to the domain), then `prisms` (a
drawn polygon extruded over a patterned level in that level's material; `sidewall_angle_deg`
is from the substrate plane, None is vertical, footprint at the bottom). `materials` are the
technology's models, copied (only the ones used) so a worker needs no PDK. `domain`:
`lo`/`hi` in dbu and `z_min_pm`/`z_max_pm`, plus per-face boundaries.

**2D (`Reduced2D`).** `background_index` everywhere in the rectangle, then `regions` painted in
order (a later region wins where they overlap): one region per drawn layer, polygons in dbu,
real effective `index`. Indices are meaningful only together with the
`EffectiveIndexReduction` that must accompany it (the record rejects a 2D problem without
exactly one, a 3D problem with one, or indices that differ from the reduction's columns).

**Boundaries.** Per face (`x_min`, `x_max`, `y_min`, `y_max`, and `z_*` in 3D):
`BoundaryCondition(kind, thickness_pm)` with `kind` one of `absorbing`, `periodic` (the opposite
face must be periodic too), `pec`, `pmc`, `symmetric` (mirror plane of structure and
excitation; the parity is an analysis choice). `thickness_pm` is the absorber depth inside the
domain, only for `absorbing`; None leaves it to the solver.

**Ports.** `PortPlane(path, layer, domain, center, width, orientation)`; `Port3D` adds
`z_min_pm`/`z_max_pm`. `path` is the layout port path (`name`, or `inst/name` through
instances), so a mapping can return to the layout port. The reference plane is the segment
through `center`, perpendicular to `orientation`, of length `width` (in 3D: that segment
extruded over the z span of the levels patterned by the port's layer, the waveguide cross-section;
the analysis chooses the larger mode-solving window). `orientation` (0 = +x, 90 = +y, ...)
points out of the device: a wave leaving through the port travels along it; incident waves
travel opposite. By default the ports of the prepared cell itself are included (paths without
`/`); a request may select others.

## Approximations

Nothing is approximated silently. The list holds:

* `EffectiveIndexReduction`: `method` (`slab_transfer_matrix`) and `method_version`,
  `polarization` (`te`/`tm`), `mode_order`, `wavelength_pm`, the vertical window
  `window_z_min_pm`/`window_z_max_pm`, one `ReducedColumn` per region (and `layer = None` for
  the background) holding the `StackSegment`s `(z_min_pm, z_max_pm, material, index)` it was
  computed from, `n_eff`, and `rule`; and a `validity` text.
* `SidewallAngleIgnored(levels)`: 2D only, listed when a level patterned by a drawn layer has a
  non-vertical sidewall.

### Effective-index method

Conventions: layers normal to z; the mode propagates in the layer plane with `beta = k0 n_eff`,
uniform in the other in-plane direction. **TE**: the field is `E_y` parallel to the layers,
`F` and `F'` continuous. **TM**: `H_y` parallel to the layers, `F` and `F'/n^2` continuous.
Order 0 is the largest `n_eff`. In the reduced 2D problem (fields uniform in z) a slab
**TE** reduction therefore corresponds to the in-plane-E polarization `(E_x, E_y, H_z)`, and a
slab **TM** reduction to the out-of-plane-E polarization `(H_x, H_y, E_z)`; a 2D solver must
be run in the matching polarization. Indices are real; `n_eff` is the index of the guided slab
mode, not the permittivity. Formulation (`slab.py`): per layer the 2x2 transfer matrix of
`(F, G = F'/p)` with `p = 1` (TE) or `n^2` (TM), evanescent cladding conditions at both ends,
and the residual `G_top + (gamma_u/p_u) F_top` as an entire function of `n_eff`. Zeros are
bracketed by a scan uniform in the transverse wavenumber and bisected (100 steps).

Per column: take the technology's vertical column (`vertical_column`) for exactly one drawn
layer (or nothing), cut it to the window (domain's vertical extent: patterned levels plus
`margins.vertical`), pad with the stack background, and treat the outermost segments as
semi-infinite. The substrate leakage and anything beyond the window are therefore ignored:
choose a window that does not reach a higher-index substrate (the reduction then has no
guided mode and warns). Materials are evaluated at the one wavelength (`refractive_index`; no
extrapolation); absorbing materials (`k > 0`) are an error.

If a column guides no mode of the requested polarization and order, `n_eff` is the larger of its
two outermost indices (`rule = "cladding_cutoff"`), the cutoff limit. This is the expected
result for the background of a fully etched strip (oxide everywhere); for a column with a
drawn layer it also gives the warning `physics.reduction.no_guided_mode`.

Limits: one wavelength only (no dispersion of the slab mode or materials); lateral confinement
and mode conversion at edges are what the 2D simulation itself resolves; sidewall slope and
radiation into layers beyond the window are ignored. Where drawn layers overlap, a region's
column is that of the layer patterning the most levels, exact only if the levels patterned by
the layers are nested (`physics.reduction.layers_not_nested` otherwise). Regions are painted
in that order (fewest levels first).

Reference values (synthetic fixture, 1.55 um, 220 nm Si in oxide): TE0 2.8478, TM0 2.0533;
the 90 nm slab column: TE0 2.1033, TM0 1.4859; oxide 1.4440.

## Port placement rule

On the port's own layer, take the polygon edges on the reference line whose outward normal
equals `orientation`, merge touching ones, and subtract stretches covered by opposite-normal
edges. The piece containing `center` is the cross-section; it must equal `center +- width/2`
exactly. Polygons that overlap rather than abut are not merged. The port must also lie inside
the domain and farther from each in-plane face than that face's declared absorber thickness.

## Preparation and request

`prepare_physical_problem(layout, technology, request) -> Result[PhysicalProblem]`. The
`PhysicalRequest` carries the problem `id`, `dimensionality`, `margins` (lateral on all sides
of the patterned bounding box; vertical above and below the patterned levels), `reduction`
(`EffectiveIndexSpec`: wavelength, polarization, mode order; required for 2D, forbidden for 3D),
`boundaries` (default absorbing), optional `cell`, `ports`, `design`, `metadata`. The service
validates the technology and layout, flattens, maps every used layer, then builds extrusions
per level (partial etch is stacked levels; blanket levels are films; gaps are background).

### Diagnostics

Besides the `technology.*`, `layout.*`, `geometry.*` and `units.*` codes of the services it
calls (their errors also prevent a problem):

| Code | Severity | Meaning |
|---|---|---|
| `physics.reduction.required` | error | 2D request without a reduction specification |
| `physics.reduction.unexpected` | error | 3D request with a reduction |
| `physics.reduction.layers_not_nested` | error | 2D: patterned levels of drawn layers are not nested |
| `physics.reduction.absorbing_material` | error | a material in the window has `k > 0` |
| `physics.reduction.material_unknown` | error | window uses a material the technology lacks |
| `physics.reduction.no_guided_mode` | warning | a drawn-layer column guides no mode; cladding index used |
| `physics.margin.negative` / `.nonpositive` / `.off_grid` | error | lateral < 0; vertical <= 0; lateral not a whole number of dbu |
| `physics.length.sub_picometre` | error | a length (margin, wavelength, level z/thickness) is not a whole number of pm |
| `physics.boundary.face_unsupported` | error | `z_*` face in a 2D request |
| `physics.boundary.periodic_unpaired` | error | one of two opposite faces is periodic |
| `physics.layer.unknown` | error | used layout layer is not in the technology |
| `physics.layer.ambiguous` | error | several technology layers match the name or GDS pair |
| `physics.layer.gds_mismatch` | error | layout and technology disagree on the GDS pair |
| `physics.layer.unpatterned` | error | shapes on a drawing layer that patterns no level (annotation layers are ignored silently) |
| `physics.grid.incompatible` | error | layout dbu does not divide the technology's manufacturing grid (or dbu) |
| `physics.grid.off_manufacturing` | warning | vertices or port centres off that grid, one per layer or port |
| `physics.extrusion.thickness` | error | level thickness not positive |
| `physics.extrusion.material_unknown` | error | level material, fill or background undefined |
| `physics.domain.empty` | error | no geometry on a layer that patterns the stack |
| `physics.port.unknown` | error | selected port path not in the flattened cell |
| `physics.port.layer_unpatterned` | error | port on a layer that patterns no level (including annotation layers) |
| `physics.port.not_on_geometry` | error | centre is not on the end of the waveguide on its layer |
| `physics.port.width_mismatch` | error | port width differs from the cross-section there |
| `physics.port.orientation` | error | port points into the geometry |
| `physics.port.outside_domain` | error | port segment not inside the domain |
| `physics.port.in_absorber` | error | port nearer a face than its declared absorber thickness |

## Physical fingerprint

`physical_problem_fingerprint(problem)` (`waffle/simulation/common/fingerprint.py`) hashes
`dbu_pm`, `description` (domain, boundaries, materials without `source`, films, prisms,
regions, indices, ports with paths) and `approximations`. It excludes `id`, `metadata`,
`schema_version`, `layout`, `technology`, `design`, `top_cell`, and `Material.source`. So a
technology revision that differs only in descriptive text, or a change outside the
domain (the substrate below the window), changes the revision but not the fingerprint; any
change to thicknesses, indices, mapping, geometry, margins, boundaries, ports, or the
reduction settings changes both.
