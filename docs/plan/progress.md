# Implementation progress

Waffle 0.1.0 demonstrates the bounded local reference lifecycle. The
[lifecycle report](../lifecycle_report.md) has measured evidence and replay
instructions. The [implementation plan](implementation_plan.md) defines the
acceptance criteria and the separate native and deployment tracks.

## Verified foundation

| Milestone | Implementation and evidence |
|---|---|
| MOD-001–002 | Greenfield decision, dependency graph, Bazel layer boundaries, isolated backend environments |
| MOD-003 | Component/interface/design/circuit/specimen/model identity, versioned records, structured diagnostics |
| MOD-004 | Integer-grid geometry, hierarchical layout, transforms, regridding, flattening, comparison |
| MOD-005 | GDSFactory generation/import/export/geometry protocols, adapter, GDS sidecars and conformance cases |
| MOD-006 | Technology/material/PDK resolution, 3D extrusion, explicit 2D effective-index reduction, physical ports and fingerprints |
| MOD-007 | Exact-revision view/channel mappings, swapped-port checks, orthogonal placement connectivity, partial response coverage |
| MOD-008 | Local immutable artifact publication, attempt history, integrity checks and provenance |
| MOD-009 | Typed capability/applicability, sampled versus validated coverage, pinned optical evaluator dispatch with partial responses |
| MOD-010 | Per-instance selection with candidate explanations, immutable dependency snapshots, exact replay and integrity checks |
| MOD-011 | Analysis requests, capability preflight, backend protocol and fake, progress/cancellation, typed acquisition extraction and completeness |
| MOD-012 | Isolated BeamZ CPU worker, 2D reduced-index propagation, persisted attempts, cancellation and retries |
| MOD-013 | GDSFactory waveguide and MMI layouts, traceable GDS, and analytic model binding |
| MOD-014 | Qualified exact-sample two-port waveguide and forward MMI responses, with immutable model evidence |
| MOD-015 | Bounded optical-power JSON ingestion, exact specimen/design/mapping association, raw and processed artifact provenance |
| MOD-016 | Bounded power attenuation fit with separate validation, comparison artifact and exact model revision |
| MOD-017 | Independently installable demo PDK and example wheels, with shared directory validation and exact registry resolution for public and restricted synthetic PDKs |
| MOD-018 | Operation fingerprints, historical and dependency replay, concurrent publication and interrupted retry |
| MOD-019 | Installed Python and CLI workflows, separate solver environment selection, and directional waveguide publication and replay |
| MOD-020 | Recorded installed lifecycle, numerical evidence, and developer handoff |
| MOD-021 | Compact 2D layout arrays and hierarchy-aware window selection. Localized 3D material assembly and the via-and-trench fixture remain open. |

## Verification

The build and 169 fast test targets passed, including layer boundary tests.
All 11 explicit backend and numerical test targets also passed. Run those
targets explicitly to keep the fast suite backend independent.

```sh
bazel test //third_party/gdsfactory:all_backend_tests \
  //third_party/beamz:smoke_test //third_party/beamz:capability_spike_test \
  //third_party/beamz:adapter_test //validation/demo_distribution:reference_test \
  //validation/beamz_characterization:real_test \
  //validation/beamz_characterization:directional_benchmark_test \
  //validation/mmi_characterization:real_test
```

The smoke tests still check the declared backend environments. Propagation,
modal extraction and binding replay have separate tests. To package the tested
local release, run `bazel run //tools:release_wheels -- --out dist/wheels` and
follow the [installation walkthrough](../designer_workflow.md).

## Supported bounds

The verified numerical result is a single 1550 nm TM sample for each synthetic
2D device. The simulated waveguide exposes all four two-port responses, and the
MMI exposes two forward responses. Broadband interpolation, 3D qualification,
and instrument validation remain open. Power calibration uses synthetic
captures and retains the simulated phase. Installed platform verification
covers Python 3.13 on macOS arm64 and in a Debian Linux arm64 container.
Native engines, automatic cache reuse and large-deployment execution remain
future work.

The [directional waveguide benchmark](../waveguide_characterization.md#bidirectional-benchmark)
tests both launches at the same exact wavelength with three meshes and separate
duration, boundary, and modal-window controls. The installed reference workflow
publishes its four complex estimates with pinned validation evidence.
