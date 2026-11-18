# Waffle 0.1.0 reference lifecycle

The recorded straight-waveguide run predates the Waffle rename. It ran on macOS
arm64 on November 17, 2026, using separate Python 3.13 environments made from the
four local wheels. The designer environment loaded GDSFactory 9.51.0, and the solver
environment ran BeamZ 0.6.2 and JAX 0.12.0.1 on CPU. The core package and demo PDK
loaded from `site-packages` outside the checkout.

## Recorded run

The example resolved a 2 µm straight guide against the demo silicon and silica
process. It generated the GDSFactory layout, published GDS with an integrity
checked sidecar, and bound the analytic model. It prepared the 1550 nm TM
reduced-index problem and ran independent fine, coarse, duration, and PML
experiments through the isolated worker. The sampled solver model was published
with its four field acquisitions, the modal projection, comparison report, and
an exactly pinned mapping. An independent CLI invocation reproduced the selected
calibrated model's complex output from the recorded experiment binding.

| Property | Measured or evaluated value |
| --- | --- |
| Frequency | 193.414489 THz |
| Analytic forward amplitude | −0.74278247 + 0.66953282i |
| Extracted forward amplitude | −0.79360789 + 0.65477338i |
| Magnitude and wrapped phase error against reference | 0.02885 and 0.04374 rad |
| Magnitude and phase change at 50 nm versus 25 nm mesh | 0.00595 and 0.08058 rad |
| Magnitude and phase change with doubled duration | 0.00936 and 0.01096 rad |
| Magnitude and phase change with thicker PML | 0.03283 and 0.02177 rad |
| Fitted backward to forward amplitude | 0.02434 |
| Synthetic fitting and validation transmissions | 0.780 and 0.784 |
| Fitted power gain | 0.73686283 |
| Held-out synthetic power error | 0.004 |
| Calibrated forward amplitude, reproduced from binding | −0.68123868 + 0.56206215i |

The forward result is one approximate sampled power-wave amplitude, within the
declared tolerances of 0.05 in magnitude and 0.15 rad in phase. Its magnitude
is slightly above unity, so the report establishes no exact passivity claim.
Reflection and reverse response remain uncomputed. Interpolation coverage and
uncertainty remain unknown. The calibrated sample meets its passive bound and
inherits the simulated phase. The separate fitting and validation captures are
synthetic, and their agreement establishes no instrument or fabricated-device
accuracy.

The 2×2 MMI has a separate effective-index simulation at the same wavelength.
Eight isolated jobs use two independent launches at each of four numerical
settings. The extraction separates forward and backward waves in the coupled
access leads and translates them to the original port planes. It publishes
`o3/o1 = 0.07268187 + 0.72296129i` and
`o4/o1 = 0.43595246 − 0.19019226i` in the localized power-wave basis. The
largest grid discrepancies were 0.01632 in magnitude and 0.12269 rad in phase.
The corresponding mirror discrepancies were 0.00786 and 0.01831 rad. The
analytic MMI continues to use its idealized quadrature response. The sampled
model leaves the other 14 responses uncomputed and rejects off-sample
wavelengths. [MMI procedure and limits](mmi_characterization.md)

The machine-readable [installed run](evidence/installed_reference_0.1.0.json)
lists exact design, layout, stack, mapping, model, dataset and artifact references.
The [waveguide comparison](evidence/waveguide_tm_1550nm.json) and
[MMI comparison](evidence/mmi_tm_1550nm.json) include the sampled reference planes
and mode-fit evidence. To reproduce the run on another machine,
follow the [installation and replay steps](designer_workflow.md). Keep the output
store if you want to evaluate the original binding. A rerun creates fresh attempts
and records the environment and artifact references it actually used.

## Acceptance commands

```sh
bazel build //...
bazel test //...
bazel test //third_party/gdsfactory:all_backend_tests
bazel test //third_party/beamz:smoke_test //third_party/beamz:capability_spike_test \
  //third_party/beamz:adapter_test
bazel test //validation/demo_distribution:reference_test \
  //validation/beamz_characterization:real_test //validation/mmi_characterization:real_test
bazel test //validation/recovery:recovery_test
bazel run //tools:release_wheels -- --out dist/wheels
```

The fast suite checks boundary visibility, model applicability, artifact
integrity, interrupted and concurrent publication, pinned replay, measurement
ingestion, and calibration. Real backend targets are explicit. The installed
waveguide and MMI examples generated GDS and sidecars. The installed `reference`
and `replay` commands used a separate solver virtual environment and reproduced
the selected complex waveguide sample. The MMI acceptance test independently
publishes the partial model, binds it, and checks replay and coverage rejection.
A separate invocation of the installed CLI reproduced both MMI responses from
that binding. The build, 162 fast targets and ten explicit backend targets passed.

## Extending the workflow

- Define a component interface and typed design parameters, pin an implementation,
  and resolve it against a technology revision. Bind layout ports to named
  terminals and model channels with exact references. The demo packages show
  this process for two components. [Reference components](reference_components.md)
- Author and validate a `Pdk` and its `TechnologyRevision` records. Distribute
  them in a separately versioned directory or package and register the exact
  technologies in `PdkRegistry`. Keep your access and licensing boundary in the
  distribution system. [Technology and PDK contracts](contracts/technology.md)
- Implement the public layout protocols or `ElectromagneticBackend` in an adapter
  package. Advertise the supported inputs and outputs, add explicit real tests,
  and keep backend selection in application assembly. The isolated worker's
  versioned protocol handles field arrays and attempt history.
  [Layout backends](layout_backends.md) · [Device analysis](device_analysis.md) · [BeamZ worker](beamz_execution.md)
- Publish the exact numerical inputs, checks, observed channels and applicability
  with a sampled model. Preserve original observations and keep comparison data
  distinct from data used to fit coefficients. [Waveguide characterization](waveguide_characterization.md) · [Calibration](calibration.md)
