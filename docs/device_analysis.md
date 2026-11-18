# Device analysis interface (MOD-011)

The analysis layer starts with an exact `PhysicalProblem` revision. Physical
preparation remains in `waffle.simulation.common.prepare`; the versioned
`AnalysisRequest` in `waffle.contracts.analysis.request` states how to excite and
measure it. `analyze()` in `waffle.simulation.photonics.device_analysis.analysis`
performs preflight, asks an `ElectromagneticBackend` to execute, then extracts
only correctly typed acquired outputs. The backend protocol is in
`waffle.simulation.common.backend` with `backend_interface_consumers` visibility,
so an adapter can implement it without a core dependency on BeamZ.

## Request and outcome

`AnalysisRequest` pins a `physical_problem` revision and optionally a
`view_mapping` revision. Its excitations are point or port-mode sources at a
positive wavelength. It carries named observables, positive integer grid spacing
in picometres, time step in attoseconds, step count, resource request (cores,
memory, wall time), and exact backend identity/version plus a versioned backend
settings payload. The backend validates its own payload keys and problem-specific
limits. Settings and resources remain in the `AnalysisOutcome.request` even on
failure or cancellation.

The observable kinds are separate: `raw_field`, `raw_flux`,
`source_normalized_flux`, and `modal_scattering`. A raw field requests one field
component, over the full domain when no port is named or at the named port plane.
Raw flux requires a port plane. A normalized result names its source excitation; modal scattering
also names the output and excitation model channels. The latter require a pinned
view mapping with zero reference-plane offset, mode 0, identity modal transform,
and power-wave normalization at the selected ports. Channels must correspond to
the selected physical ports. A response marked `uncomputed` in the mapping can
be acquired; `AnalysisOutcome.complete` becomes true only when it actually is.
No conversion from a raw field or flux to a normalized response is implicit.

Each `Acquisition` has an intrinsic kind, payload, SI units, named axes, and
shape. Extraction accepts a result only when its name and kind match the request,
the payload is finite, numeric, nonempty, exactly shaped, and its units match the observable. Electric fields use
`V/m`, magnetic fields `A/m`, raw flux `W/m` in 2D or `W` in 3D, and normalized quantities
dimensionless `1`. Backend adapters must supply exact sampled coordinates and
reference-plane orientation in their payload or a richer backend-specific type;
the current generic interface does not derive those from shape. `Acquisition`
checks the complete numeric array shape and finiteness.

`AnalysisOutcome` has a state (`rejected`, `complete`, `partial`, `cancelled`,
`failed`), acquired subset, missing requested names, completed step count,
backend identity/version, request, and diagnostics. A backend's `complete` claim
is downgraded to `partial` if a requested output is absent/invalid, a diagnostic
is an error, or completed steps differ from the request. Cancelled and failed
attempts preserve valid partial acquisitions for inspection. Artifact publication
and append-only attempt persistence belong to MOD-012's worker, outside this
in-memory interface.

## Preflight and execution

Preflight collects structured diagnostics without running the backend. It checks
problem and mapping revisions; dimensionality and 2D polarization (which must
match the effective-index reduction); each excitation's wavelength against the
single-wavelength 2D reduction; source mode order, point location, ports,
boundaries, field components, observable kinds, settings version, progress and
cancellation support, logical channel correspondence, and normalization
conventions. The backend also receives `validate_problem(problem, request)` for
material, geometry, mesh, stability, source, and monitor limitations that a
generic capability set cannot express. A rejected request never calls `execute`.

`BackendCapabilities` exposes the static supported sets and exact backend
version. `execute(problem, request, progress, cancellation)` receives an ordinary
callback and cancellation token and returns a `BackendRun`. An already-cancelled
request returns `cancelled` before launch. A worker can poll a token between
chunks and report `ProgressEvent(completed_steps, total_steps, phase)`; this
interface does not promise interruption of a running JAX compile or kernel.
Exceptions from programmer errors remain exceptions; expected solver failures
should be returned as `BackendRun(state=failed, diagnostics=...)` with any valid
partial acquisitions.

The test-only `FakeBackend` exercises success, unsupported preflight, versioned
settings, progress, cancellation, partial output and failure, stale revisions,
channel and reference-plane validation, and extraction without solver imports.
Run:

```sh
bazel test //waffle/contracts/analysis:request_test \
  //waffle/simulation/photonics/device_analysis:analysis_test
```

## BeamZ handoff

The bounded BeamZ findings are in `docs/beamz_capabilities.md`. A future
adapter can begin with 2D raw fields from a point excitation: it should convert
Waffle coordinates to BeamZ's centered public frame, use the matching 2D
polarization, request raw monitor data, and return explicit SI units and axes.
It should advertise only those capabilities it has validated. Mode-source
calibration, raw flux orientation, source normalization, and modal scattering
need separate numerical evidence before the adapter advertises them. The worker
must record actual BeamZ/JAX versions, effective mesh/time settings, termination
reason, and published artifacts for each attempt.
