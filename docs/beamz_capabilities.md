# BeamZ 0.6.2 capability spike

This is evidence for MOD-011/012 interface design, not a Waffle adapter or a scattering
validation. The exercised package is the Bazel pin `beamz==0.6.2` on Python 3.13,
macOS arm64, JAX CPU. The upstream source inspected was the installed wheel under
`@pip_beamz`, especially `beamz/simulation/api.py`, `beamz/devices/sources/specs.py`,
`beamz/devices/monitors/monitors.py`, `beamz/devices/boundaries.py`,
`beamz/simulation/results.py`, and `beamz/lattice.py`. The upstream project is
[beamzorg/beamz](https://github.com/beamzorg/beamz); the wheel used by this repo is fixed
by `third_party/beamz/requirements.lock`.

Run the bounded real-backend probe explicitly:

```sh
bazel test //third_party/beamz:capability_spike_test --test_output=all \
  --nocache_test_results --test_arg=-s
```

The probe uses a 2.4 × 1.2 µm vacuum domain, 0.1 µm cells, 100 samples at 0.1 fs,
2D TM, a Gaussian pulse near grid x=0.5 µm, 0.2 µm PML, and the JAX backend. On this
machine it passed in 5.4 s of Bazel test time (4.45 s pytest). The `Ez` recorder returned
20 frames of shape `(13, 25)` in `(y, x)` storage order. At grid columns 8 and 16, the
maximum-over-y field peaks occurred at recorder frames 2 and 8 respectively, with peak
magnitudes `1.487e-5` and `7.663e-6`. These checks show finite, delayed propagation at
the intended grid positions; they do not establish waveguide accuracy or energy balance.
One raw `FieldMonitor` returned complex `Ez` samples of shape `(1, 9)` at
`c / 1.55 µm`; a `FluxMonitor` returned one finite flux value (`1.773e-21`) through
`get_dft_flux()`. That tiny flux is uncalibrated pulse evidence, not transmission.

## Mapping to Waffle's physical problem

* Lengths in the public BeamZ API are metres; time is seconds; DFT frequencies are Hz.
  Waffle `dbu_pm` and `wavelength_pm` need explicit conversion. `Material` accepts
  relative permittivity (`epsilon_r` or `permittivity`), so a lossless reduced index
  becomes `epsilon_r = n_eff**2`, not `n_eff`.
* Public geometry and device coordinates are `(x, y, z)`. A `Simulation(domain=(Lx,Ly))`
  has a centered public coordinate frame: the test's source x of `-0.7 µm` maps to
  grid x `0.5 µm`. Waffle's domain `lo`/`hi` requires translation by its physical
  midpoint. Results expose `metadata.coordinate_offset` and exact sampled regions;
  extraction should use those rather than infer coordinates from array shape.
* For `plane_2d="xy"`, BeamZ `polarization="te"` advances in-plane electric
  `(Ex,Ey,Hz)`, and `"tm"` advances out-of-plane electric `(Hx,Hy,Ez)` (the latter
  was exercised). Thus Waffle's **slab TE reduction maps to BeamZ 2D `te`** and slab
  **TM reduction maps to BeamZ 2D `tm`**. This follows the conventions in
  `docs/contracts/physics.md`; the two reductions are not interchangeable.
* `Design` carries a background and painter-ordered structures. BeamZ also accepts a
  raster `scene`/`raster_grid` or `material_grid`. A future adapter must explicitly
  handle Waffle region paint order, polygon holes, domain translation, and grid
  discretization. None of those geometry paths was exercised by this probe.
* `PML(edges=..., thickness=...)`, `Absorber`, `PEC`, and paired-axis `Periodic`/`Bloch`
  exist. BeamZ's omitted boundary default is all-edge PEC. Waffle's per-face
  `absorbing` can be represented with explicit PML or absorber selection; its
  `periodic` faces require a matched axis. `pmc` and `symmetric` have no direct
  public boundary class in the inspected API, so reject them until a validated
  mapping exists. PML's default thickness is 12 cells; the probe specified a
  physical thickness. Periodic JAX support is documented in the installed class,
  but was not run here.

## Sources, monitors, and output status

`GaussianSource(position, width, signal)` is a simple sampled spatial source and
excites the active out-of-plane component (`Ez` for TM, `Hz` for TE). It does not
represent a calibrated port incident mode. `ModeSource(center, size, source_time,
direction, mode_spec, power)` has a finite plane and signed propagation direction;
`source_time` must supply sampling and a carrier frequency. Its mode solver and
calibration need a separate, larger validation before using it for Waffle ports.

`FieldRecorder` yields time-domain `fields` with `field_times` and `field_steps`.
`FieldMonitor` yields complex `dft_fields` and `dft_frequencies`; `FluxMonitor`
can yield DFT field components and flux through `get_dft_flux()`. In this 2D
test, the `FluxMonitor` constructor required exactly one zero size extent, so
its x-normal plane used a nonzero z size even though the simulation is 2D.
The `power_spectrum` storage array was empty in this run while
`get_dft_flux()` returned a value. Extraction should use BeamZ's public result
accessors and test their shape, units, and orientation. The probe explicitly set
`normalize_source=None` to retain raw acquisitions; the default is source index 0.
`SimulationResults.renormalize(source)` can derive another view from retained raw
acquisitions, but this test did not validate a normalized value. A raw flux, a
source-normalized flux, and a modal scattering amplitude must remain distinct
observable types in MOD-011.

## Interface recommendations and remaining validation

Use a capability query keyed by dimensionality, polarization, material model,
boundary kinds, source type, monitor type, and desired observable. Reject unsupported
combinations before worker launch. Carry explicit metre/second/Hz conversions,
coordinate offset, mesh and time settings, normalization choice, BeamZ version,
backend selection, and result completeness into the attempt record. For a 2D reduced
problem, require the matching effective-index polarization and record the reduction
revision and wavelength.

`Simulation.run()` returns detached results. `advance(state=..., num_steps=...)` and
`AutoTermination` provide segment and bounded-stop mechanisms in the installed API;
they were inspected but not exercised. Treat cancellation as a worker lifecycle
operation at chunk boundaries, with an inspectable partial attempt. Do not promise
instant interruption of a JAX compile or kernel. Keep raw acquisitions separate from
postprocessing, so extraction can be retried and missing monitors can be reported.

Before normalized S parameters are advertised, validate mode launch and monitor
orientation at each port, reference-plane phase and direction, source normalization,
incident/reflected separation, convergence with grid/time/PML, and a known reference
device. The present probe establishes only a bounded CPU field simulation and raw
monitor access.
