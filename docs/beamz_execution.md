# Bounded BeamZ execution (MOD-012)

`third_party.beamz.adapter.BeamZBackend` implements the public electromagnetic
backend interface. It imports BeamZ only after preflight. The supported runtime
is BeamZ 0.6.2 and JAX CPU, on the repository's Python 3.13 backend environment.
The installed optional dependency is `waffle-eda[beamz]`.

## Supported physical/numerical subset

The adapter accepts a lossless scalar `Reduced2D` problem, a single point
excitation, the reduction's exact wavelength and polarization, raw scalar fields
only (`Ez` for slab TM, `Hz` for slab TE), and four equal explicitly specified
absorbing boundaries. Boundaries map to BeamZ PML, at least two cells thick and
less than half either domain extent. Modes other than zero, port-mode sources,
3D/dispersive/anisotropic materials, raw flux, normalized flux, and modal
scattering are rejected. No raw acquisition is relabeled as a scattering channel.

Domain extents must divide the requested isotropic spacing exactly. Grid counts
are bounded to at least eight cells per axis and at most one million cells;
indices are in [1,10]. A conservative 2D CFL check precedes allocation. The
material raster samples polygon interiors at cell centres in `(y,x)` array
order, painting background then regions in their declared order, with
`epsilon_r = n_eff**2`. Geometry contracts have no holes; self-intersecting
polygons are rejected. This binary raster has no subpixel smoothing/convergence
claim. Lengths use metres, source wavelength picometres is converted explicitly,
and attoseconds become seconds. A centred BeamZ `MaterialGrid` and source/port
translation preserve Waffle's arbitrary domain origin.

Settings schema 1 permits positive integer `source_width_pm`, `pulse_center_as`,
`pulse_width_as`, and `record_interval` only. Defaults are two cells source
width, six carrier periods pulse centre, two periods Gaussian width, and ten
steps per recorder frame. The signal is
`exp(-((t-centre)/width)**2) * cos(2*pi*f*(t-centre))`. It is an uncalibrated
sampled source, with no incident power-wave claim. Source width lies between one
cell and a quarter of the smaller domain dimension; source centre is outside
PML. Request enough time to contain the pulse and propagation of interest.

A port raw field uses an axis-aligned public `FieldMonitor` at the named physical
port. Returned complex arrays have axes `(frequency,transverse)`; full-domain
recorders have `(time,y,x)`. Electric fields use `V/m`, magnetic fields `A/m`.
Port arrays use the public `get_dft_component` accessor: it divides accumulated
sums by their temporal weight and applies the real-field amplitude factor,
without source normalization. The temporal weights and factor are recorded.
The adapter records exact public monitor grid points, the scalar Yee support,
physical sampled plane, outward orientation, frequency and full-field coordinate
arrays. The actual uniform-grid BeamZ result has `sample_region=None`; the
adapter therefore uses the public monitor `get_grid_points_2d` sampling API,
checks its count against acquired data, and includes node/centre offsets.
These coordinates describe actual samples and can differ from requested planes.
The DFT kernel was checked in BeamZ's installed `observe._accumulate_dft`: it
accumulates with `cos(theta)+i*sin(theta)`, so metadata pins
`exp(-i omega t)` and a forward phase `exp(+i beta distance)`.

## Isolated worker and persistence

The solver-neutral controller is `waffle.execution.worker.run_isolated`:

```python
result = run_isolated(problem, request,
    command=(sys.executable, '-m', 'third_party.beamz.worker'),
    root=Path('runs'), store=store, cancellation=token)
```

`ExecutionResult` contains `attempt_id`, persistent `directory`, `run`
(`BackendRun`), optional completed `artifact`, and `effective` metadata. Bazel
launchers use the separate `//third_party/beamz:worker` executable as `command`.
The command receives the absolute attempt directory as its final argument.
Core execution has no BeamZ import or dependency.

The explicit version-1 protocol consists of immutable input `request.json`
(`WorkerRequest`), `.npy` numeric payloads, and atomically linked
`response.json` (`WorkerResponse`). Unknown protocol versions/fields fail. A
response cannot overwrite an existing response. Arrays are non-pickled, numeric,
finite, shape checked and must match requested kinds/units. Complete publication
also requires every requested output and the exact step count, without errors.

Each call creates a fresh ArtifactStore attempt and work directory, retains
stdout/stderr, inputs, worker environment before solver launch, response/arrays if available, and controller outcome.
Success atomically publishes a `simulation_fields` artifact containing request,
response (including effective settings), and named arrays. Failed, rejected,
partial and cancelled runs publish no complete artifact. Retrying uses a fresh
attempt; identical successful artifact content is idempotent and existing
completed bytes remain immutable. Logs/workspaces are retained for inspection,
with no automatic cleanup. An abruptly killed controller leaves a started
attempt, identifiable as interrupted; it never pretends completion.

Effective metadata includes exact request/problem digest, BeamZ/JAX versions,
Python/OS, CPU devices, realised raster shape/spacing, timestep/step count,
waveform settings, source normalization status, sampling coordinates, and
termination. The operation also records the configured command and controller
environment. This is inspectable provenance, not a cache-reuse qualification.

Wall time is enforced by the controller. Live cancellation sends SIGTERM to the
worker process group, then SIGKILL after two seconds if necessary, including a
running compile/kernel. In-process cancellation is checked before launch and
after the run; progress callbacks describe launch/completion only. CPU request
sets OMP threads, but JAX may create other threads. Memory request is checked
against a conservative raster/recorder estimate, not an operating-system RSS
limit. GPU/distributed execution is unsupported.

## Numerical and recovery evidence

Run explicitly:

```sh
bazel test //third_party/beamz:adapter_test --test_output=all --test_arg=-s
bazel test //waffle/execution:worker_test \
  //waffle/execution:worker_boundary_test //waffle/execution:protocol_boundary_test
```

The real test uses a translated 6 × 2 µm domain (`lo=(1,2)` µm), a 500 nm
wide straight guide (`n=2.4`, background `1.44`), 50 nm cells, 0.1 fs timestep,
1600 steps and 400 nm PML. TM `Ez` time records have shape `(160,41,121)`;
material shape is `(40,120)`. Fields at x=3 and 5 µm peak at steps 400 and
570 respectively, demonstrating delayed positive-x propagation at the intended
coordinates. Maximum complex DFT magnitudes are `8.523329270e-7` and
`9.243346565e-7 V/m`. The scalar profile least-squares ratio is
`0.773932453 - 0.843722127j`, with profile residual `0.186304517`.
The magnitude exceeds one and the residual exceeds the characterization
threshold: this fixture proves raw propagation and units/orientation, and does
**not** qualify a normalized transfer or scattering model. A separate real TE/Hz run returns `(160,40,120)` on cell-centred support,
with maximum time-domain field `7.1693335e-10 A/m`; port DFT magnitudes are
`5.502294400e-11` and `7.089325647e-11 A/m`. The actual TE near monitor
plane is x=3.025 µm after its Yee half-cell shift. The separate real
worker round trip returned BeamZ `0.6.2`, JAX `0.12.0.1` on CPU.

Focused tests cover painter overrides, shifted coordinate orientation, CFL,
wavelength/mode/settings/boundary rejection, subprocess exception diagnostics,
live cancellation, enforced timeout, unsupported protocol versions, atomic
no-overwrite response publication, fresh retry attempts and unchanged previously completed
artifacts. Existing `smoke_test` and `capability_spike_test` targets are preserved.
