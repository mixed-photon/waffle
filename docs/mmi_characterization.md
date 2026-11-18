# Bounded 2×2 MMI characterization

`characterize_mmi` in `//waffle/simulation/photonics/characterization:mmi`
qualifies two complex transmission elements for the demonstration MMI under the
vertical TM effective-index reduction at exactly 1550 nm. It accepts the prepared
`PhysicalProblem`, template `ModelRevision`, exact `ViewMapping`, BeamZ worker
command, run directory, and artifact store. The recipe checks the four original
port planes, transverse polygon symmetry, and the mapping's mode-zero identity
and power-wave convention before acquiring fields.

## Acquisition and normalization

The original port planes are 25.5 µm apart. The analysis extends their uniform
500 nm access strips, centered at ±650 nm, by 11 µm through the longitudinal PML.
Each of the two independent point launches is 8 µm upstream of the left
reference plane and aligned with its respective strip. Eight distinct exterior
planes on each side sample coherent scalar `Ez`, using the actual coordinates
reported by BeamZ 0.6.2. The coherent time convention is `exp(-i omega t)`.

The version 1 extraction projects every transverse field onto the even and odd
analytic lateral slab modes of the coupled strip pair. Longitudinal fits
separate forward and backward components and translate them to the original
port planes. The fitted component is scaled by the square root of its
propagation index, consistent with scalar `Ez` power proportional to
β times the squared transverse norm. Equal sum and difference combinations
then give the localized power-wave basis. The two launch columns measure the
incident waves in both strips. Inverting that measured incident matrix removes
unintended incidence in the adjacent strip. The mirror check compares
independent launches against reflection symmetry of the exact polygons.

| Configuration | Grid | Steps at 40 as | PML |
|---|---:|---:|---:|
| Fine | 20 nm | 10,500 | 1 µm |
| Coarse | 25 nm | 10,500 | 1 µm |
| Extended duration | 20 nm | 18,000 | 1 µm |
| Thicker boundary | 20 nm | 10,500 | 1.5 µm |

Each configuration acquires two launches with 16 coherent profiles per launch.
Magnitude and wrapped phase must agree within 0.05 and 0.15 rad for each
observed pair under the grid, duration, boundary, and mirror comparisons.
Reducing the projection window from ±1.65 µm to ±1.3 µm must agree within
0.01 and 0.02 rad. The incident matrix condition number must be ≤2.
The mirrored complex launch discrepancy, right-side incident amplitude fraction,
and maximum longitudinal fit residual must each be ≤0.05. Fitted indices must
stay clear of the ±3% search limits. The fine-grid guided power sum for
`o1` may exceed unity by at most 0.05.

## Publication and replay

Numerical qualification produces an immutable `mmi_transfer` artifact and
`validation_report`. A passing report publishes a model revision and mapping
for exactly `o3/o1` and `o4/o1`, written as output/excitation. The other 14
responses are uncomputed. Reflection estimates and the second launch serve as
qualification diagnostics. If extraction or qualification fails, the evidence
and failed report remain available and no executable model is published.

The model pins evaluator `waffle.mmi.samples` version `1`. Use
`sample_evaluator(store, model)` to restore it from transfer evidence and
`evaluate_optical_scattering` to request the observed pairs. The experiment
binding pins the exact model, mapping, conditions, and channel subset. The
installed replay path restores the evaluator from that binding. See
[Experiment bindings and replay](experiment_bindings.md).

The result applies to the effective-index 2D slab and coupled-lead basis under
the specified extension and boundary assumptions. The evidence contains one
frequency, with no interpolation or held-out frequency coverage. Guided power
does not account for radiation. The two-grid, duration and boundary comparisons
measure sensitivity at the stated settings and do not assign uncertainty,
certify exact passivity, or establish agreement with fabricated or 3D devices.

Run the real acceptance test to reproduce the field acquisitions, publication,
and experiment replay.

```sh
bazel test //validation/mmi_characterization:real_test --nocache_test_results
```

The test writes `test.outputs/measured.json`, the immutable `test.outputs/store/`,
and retained worker attempts in `test.outputs/workers/`. The machine-readable
summary includes the observed complex responses, all qualification comparisons
and exact artifact identifiers.

## Recorded result

All 46 qualification checks passed across eight worker attempts and 128
coherent profiles. At 193414489032258.06 Hz the replayed amplitudes were
`0.072681868 + 0.722961287i` for `o3/o1` and
`0.435952457 − 0.190192261i` for `o4/o1`. Replay rejected requests for all
14 uncomputed responses and the 1551 nm operating point.

| Comparison against fine grid or its mirror | Maximum magnitude discrepancy | Maximum phase discrepancy (rad) |
|---|---:|---:|
| Coarse grid | 0.016319 | 0.122688 |
| Extended duration | 0.001341 | 0.004425 |
| Thicker PML | 0.002492 | 0.011091 |
| Narrower projection window | 0.004120 | 0.002426 |
| Mirrored launch | 0.007859 | 0.018306 |

The worst incident-matrix condition number was 1.01873, the largest relative
complex mirror-launch discrepancy was 0.02583, and the maximum right-side
incident fraction was 0.02836. The largest standing-wave fit residual was
0.01794. No fit reached the index search limit. Fine-grid guided power for
`o1` was 0.759744. [Structured numeric report](evidence/mmi_tm_1550nm.json)
