# Optical power calibration

`calibrate_power_gain` fits one constant passive attenuation factor for an
observed forward channel of a pinned simulation model. The model, mapping,
physical problem, and fabricated design must agree. Each dataset must pin its
specimen and the same mapping and design. The fit and validation use distinct
original captures and dataset identities. The importer checks their artifacts
and metadata before the comparison uses any data.

The supported observation is linear transmitted power in W, mW, or µW. The
capture records positive `incident_power`, an instrument calibration method and
reference, and a wavelength axis in whole picometres after unit conversion.
Validation and fitting share the same fixed operating conditions. The simulation
must be executable at every observed wavelength. Synthetic captures are marked
as such throughout the artifacts and comparison report.

For simulated complex amplitude `S`, measured power ratio `T`, and fitted
power gain `g`, the model fits `T ≈ g |S|²` by unweighted least squares. The
prior restricts `g` to an explicit interval inside `[0, 1]`. Small simulated
power can make attenuation unidentifiable. The comparison records the
unconstrained gain and the applied gain, the prior bounds, each measured
power ratio, the simulated and calibrated amplitudes, and the independent
validation error limit. Validation uses the maximum absolute transmitted-power
error. The published calibrated amplitudes also satisfy the passive bound.

The calibrated amplitude is `sqrt(g) S`. Its phase comes from the simulation.
Power-only data evaluates power agreement at the listed wavelengths and
conditions. It supplies no phase uncertainty or interpolation coverage. A
supplied standard deviation is converted to a power-ratio deviation. Unknown
uncertainty remains unknown, and a recorded zero remains zero.

The comparison artifact links the base model, fitting and validation datasets,
and both data artifacts. A failed check leaves the comparison inspectable and
returns a diagnostic with its artifact reference. A passed check returns a new
model revision and repinned mapping. The model exposes just the fitted channel
at its exact samples. `calibrated_evaluator` reads the pinned comparison to
replay those values through the public optical evaluator. Publishing an
experiment binding retains the base simulation, both measurement records and
their specimens, and the referenced immutable artifacts.

`bazel test //waffle/characterization/calibration:power_test` checks the fitted
gain, measurement provenance, the separate validation outcome, exact sampled
evaluation, and rejection of overlapping captures or missing incident power.
The installed reference example uses simulated power with synthetic attenuation
and two synthetic captures. Its agreement demonstrates the workflow on the
fixture.
