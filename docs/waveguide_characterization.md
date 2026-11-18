# Bounded uniform waveguide characterization

The implementation targets MOD-014's first bounded case: one forward coherent
transfer between two reference planes in a uniform straight waveguide. It does
not claim MMI support, a full S matrix, reverse propagation, or reflections.

`//waffle/simulation/photonics/characterization:waveguide` exports
`extract_profile_ratio`. Inputs are complex arrays with axes `(frequency,
transverse_coordinate)` on identical physical transverse sampling grids. Each
frequency uses the complex least-squares scalar
`vdot(upstream, downstream) / vdot(upstream, upstream)`. The normalized residual
`norm(downstream - scalar * upstream) / norm(downstream)` measures profile
consistency. This cancels a common source amplitude. It does not separate incident
and reflected fields. A zero profile, nonfinite value, shape mismatch, or residual
above the selected threshold produces a structured diagnostic. Coordinates must
come from the solver's sampled-region metadata; requested port coordinates alone
are insufficient when the solver snaps planes to a grid.

The version `uniform_profile_ratio_v1` pins extraction, `exp(-i omega t)` time
convention and `same_source_profile_ratio` normalization. A positive forward phase
is compared with `exp(+i 2*pi*f*n_eff*distance/c)`. The reference index must describe
the actual reduced geometry and polarization. An arbitrary nominal index is not a
valid physical reference. Frequency is computed once as
`299_792_458.0 / (wavelength_pm * 1e-12)` and carried through evidence, report and
operating `Quantity(..., "Hz")` without rounding.

`validate_waveguide` in target `:validation` compares every exact fine-grid sample
against both a supplied analytic reference and an independently acquired coarser
grid sample. Magnitude and wrapped phase errors have separate explicit tolerances.
The same physical planes, component, channels and time convention are required.
The resulting versioned `ValidationReport` lists observed and uncomputed matrix
elements. A two-grid discrepancy is not an uncertainty estimate or evidence of
asymptotic convergence. Time-window and boundary convergence must be assessed
separately. There is no interpolation and no interval coverage claim; therefore
there are no purported held-out interpolation results.

`publish_waveguide` in target `:publication` always publishes immutable raw
`waveguide_transfer` and `validation_report` artifacts. It publishes an executable
model only when the report passes and the caller supplies the modal qualification
rationale. That rationale must cite physical evidence of a single forward guided
mode, consistent cross-section and transverse weighting, matched reference planes,
and negligible launch/end reflections. Scalar field profiles alone cannot prove
those conditions. Without such evidence, the correct result is published raw
transfer evidence with `model=None`; it must not be advertised as normalized
power-wave S21. Publication records a versioned operation and typed input links;
pass all raw solver artifacts, requests and physical problem revisions as `inputs`.

For qualified models, only the observed forward channel is executable. The payload
is pinned to `("waffle.waveguide.samples", "1")`; `sample_evaluator(store, model)`
restores it from the persisted model evidence. Register that callable with
`evaluate_optical_scattering`. Exact-frequency applicability rejects other samples.
A new model revision requires a new mapping revision pinned to it before experiment
binding. Existing immutable binding snapshot publication/replay then carries that
model and all artifact dependencies. Caller assembly owns mapping and experiment
identities; extraction does not silently reuse the analytic model's mapping pin.

## Bidirectional benchmark

The directional benchmark uses the same 2 µm, 500 nm wide, 1550 nm TM reduced
guide with 10 µm uniform access extensions on both ends. It uses an independent
launch 8 µm from each reference plane and records eight coherent `Ez` profiles
around each plane. The access lengths and transverse profile match on both sides,
so the modal power factors cancel in the scattering estimate.

Each profile is projected onto the fundamental lateral slab mode. A joint fit
estimates one propagation constant near the analytic slab value and separates
the positive- and negative-x waves in each window. The two windows fit
independent amplitudes at their respective reference planes. The two launches
supply the columns of the incident matrix, and the measured outgoing waves
satisfy `B = S A`. Solving for `S` accounts for residual incidence from the far
boundary. The fit leaves the transmitted and reflected amplitudes unconstrained.
In particular, the procedure does not clip amplitudes or impose passivity.

The explicit CPU test compares 25, 20, and 12.5 nm meshes at a fixed 25 as time
step. It compares 200 and 300 fs durations, 1 and 1.5 µm absorbing layers, and
projection windows with widths of 2 and 3 µm. Acceptance covers magnitude and
phase of both transmissions, reflection magnitude, reciprocity, fitting
residuals, and the largest possible power gain of the estimated matrix. The
reflection reference is zero, so reflection phase has no analytic comparison.

```sh
bazel test //validation/beamz_characterization:directional_benchmark_test
```

The test writes the report, exact worker references, and fitted estimates to
`test.outputs/directional.json`, and publishes the same evidence in its
artifact store. The directional benchmark is separate from the 0.1.0
published model and its historical replay. The benchmark covers a single
frequency of a uniform 2D guide. It supplies no fabricated-device or
interpolation validation.

On macOS arm64, all 48 comparisons passed across the ten BeamZ worker runs.
The 12.5 nm mesh gave transmission magnitudes of 1.0000193 and 1.0000186
and a maximum reflection amplitude of 0.0000151. The transmission phase
disagreed with the slab reference by at most 0.00597 rad and changed by at
most 0.01982 rad across the mesh controls. The largest excess of squared
singular value above unity was 0.00445% on the finest mesh and 0.04890%
across all controls, within the 0.1% regression limit. These are numerical
estimates, with small residual power error.

On the same finest-mesh left launch, the ratio of the fitted total fields
at the endpoint planes had magnitude 1.0020604. With the thinner absorbing
layer it fell to 0.9862291 while the separated transmission was still
approximately unity. This sensitivity explains why a total-field ratio can
look like gain or loss even for a lossless uniform guide.
[Structured evidence](evidence/waveguide_directional_tm_1550nm.json)

Fast checks:

```sh
bazel test //waffle/characterization/validation:compare_test \
  //waffle/simulation/photonics/characterization:waveguide_test \
  //waffle/simulation/photonics/characterization:publication_test
```

The publication test uses a labeled synthetic scalar-profile fixture to verify
artifact integrity, persisted evaluator reconstruction, public API evaluation and
out-of-sample rejection. Its passing qualification string is algebraic fixture
data, not measured BeamZ evidence. Real numerical evidence must be published by an
explicit backend integration workflow; none of these fast tests claims solver
accuracy.

## Measured isolated-worker recipe

`characterize_waveguide` in target `:recipe` accepts a prepared rectangular
`PhysicalProblem`, template `ModelRevision`, its `ViewMapping`, worker `command`,
work-directory `root`, `ArtifactStore`, and optional cancellation token. It returns
`.publication`, `.report`, `.mapping`, `.executions` and longitudinal fit diagnostics.
An unqualified numerical result still publishes raw evidence and the report, with
no model or mapping. Solver failure identifies its retained attempt directory.

The tested example uses `waveguide(length_um=2, polarization=Polarization.TM)` at
1550 nm. This is the vertical TM effective-index reduction, hence scalar `Ez` in
2D and the lateral TE slab equation (`F,F'` continuous). The analytic reference is
`n_eff=1.8470165586423828`. The original design/layout endpoints are the two model
reference planes. The derived analysis problem extends the same uniform rectangle
10 µm to the left and 6 µm to the right, through the x PML, with a Gaussian source
8 µm upstream of the first plane. These extra leads are an analysis assumption,
not a change to the fabricated design. Each derived problem pins the original
physical problem in metadata and in publication provenance. Actual sampled plane
separation must equal the original design length; a common grid translation is
harmless only because the entire calibration segment is uniform.

`modal_overlap_ratio_v1` projects each coherent transverse profile onto the real
analytic fundamental slab profile, cosine in the core and exponentially decaying
in the cladding. The overlap uses unit weight for lateral TE (`Ez`) and `1/epsilon`
for lateral TM (`Hz`), following the self-adjoint slab equation in
`device_analysis/slab.py`. Nonzero resolvable modal coefficients are required.
The downstream/upstream coefficients have the same cross-section and modal power
factor, so their ratio is an inferred forward power-wave amplitude under the
reported single-mode and low-backward-wave approximation. Radiation in the raw
profile remains explicitly unmodeled. This is a 2D reduced-index result; it does
not validate a 3D fabricated product.

Four independent worker processes run the fine 25 nm grid, coarse 50 nm grid,
doubled duration, and changed PML thickness. The fine time step is 50 as with 4000
steps; the time comparison uses 8000 steps. The transverse domain is ±3 µm and
PML thickness is 1 µm, compared with 1.5 µm. Five equally spaced longitudinal
planes use 3 µm transverse windows. A second projection truncates to 2 µm to test
window sensitivity. A two-direction longitudinal least-squares fit scans 1001
propagation indices within ±15% of the analytic reference. The index search is
bounded to avoid the direction aliases possible with equally spaced planes.
The fit is diagnostic, not a general port-mode decomposition.

Qualification requires absolute magnitude error ≤0.05 and wrapped phase error
≤0.15 rad against the analytic reference and each grid/time/PML comparison;
backward/forward fitted amplitude ≤0.05; fit residual ≤0.05; and projection-window
sensitivity ≤0.01 in magnitude and ≤0.02 rad. The analytic guide must have exactly
one lateral guided mode. Default TE/500 nm can have another lateral mode and is
not qualified by this recipe. The original unprojected profile residual is
reported without pretending radiation vanishes or equating it to modal error.

On the measured TM run, all checks passed: analytic magnitude/phase discrepancies
were 0.02885/0.04374 rad; grid 0.00595/0.08058; duration 0.00936/0.01096; PML
0.03283/0.02177; backward amplitude fraction 0.02434; longitudinal residual 0.00343;
projection-window differences 0.000918/0.000946. The fitted index was 1.84923.
Only forward `o2/o1` at the exact sampled frequency is published as executable;
all three remaining matrix entries are marked uncomputed in the new mapping.
The model evidence pins all four raw field artifacts plus its extraction and
validation artifacts. The real test restores the model from JSON and evaluates
this observed sample through `evaluate_optical_scattering`.

Reproduce the complete measured evidence, retained worker attempts and local
immutable artifact store:

```sh
bazel test //validation/beamz_characterization:real_test \
  --test_output=all --nocache_test_results
```

The test's `test.outputs/measured.json` and `test.outputs/store/` contain the
machine-readable report and published artifact bundles. They are numerical
acceptance outputs, separate from fast checks.

The recorded machine-readable run is [waveguide_tm_1550nm.json](evidence/waveguide_tm_1550nm.json). Its observed forward amplitude is `-0.7936078861 + 0.6547733808j` (magnitude 1.02885). This finite numerical excess over unity is within the stated amplitude tolerance; the publication does not certify exact passivity or assign a zero uncertainty.
