# Agent guide

Read `ARCHITECTURE.md` for layering. Plan: `docs/plan/implementation_plan.md`.

## Layout

* `waffle/` import package. `waffle/contracts/foundation/` holds canonical JSON and hashing,
  entity and revision identity, structured diagnostics, units and quantities, `Result`,
  and versioned immutable `Record`s with migration.
* `waffle/contracts/component/` parameter schemas and values (design, operating, fitted),
  interface, component, implementation, resolved design, circuit, and specimen records.
* `waffle/contracts/models/` the `ModelRevision` record. Details: `docs/contracts/identity.md`.
  Typed capabilities and applicability are in `capability.py`; `waffle/models/`
  checks applicability and dispatches pinned optical evaluators
  (`docs/contracts/model_capabilities.md`).
* `waffle/design/components/` resolving a design; `waffle/design/instances/` building and
  validating circuits.
* `waffle/contracts/mappings/` exact view/channel correspondences;
  `waffle/design/mappings/` revision, geometry, connectivity and response-coverage
  checks (`docs/contracts/mappings.md`).
* `waffle/contracts/technology/` technology revision, process stack, material, and PDK manifest
  records. `waffle/technology/` validation, layer mapping, fingerprint, PDK registry;
  `waffle/pdk/` PDK validation and directory export; `waffle/pdk/testing/` synthetic fixture PDK
  for tests (`docs/contracts/technology.md`).
* `waffle/contracts/geometry/` integer-dbu `Grid`, `Transform`, shapes, and the shared
  `Unsupported` diagnostics; `waffle/geometry/services/` unit conversion, paths, placements.
* `waffle/contracts/layout/` the `Layout` record (cells, instances, layers, ports);
  `waffle/layout/services/` validate, flatten, regrid, compare, physical units
  (`docs/contracts/layout.md`).
* `waffle/contracts/physics/` the `PhysicalProblem` record and approximations;
  `waffle/simulation/common/` layout-and-technology to physical problem, port placement, physical
  fingerprint; `waffle/simulation/photonics/device_analysis/` slab solver and effective-index
  reduction (`docs/contracts/physics.md`).
* `waffle/layout/backend/` the layout backend interface (generation, import, export, and geometry
  operations as separate protocols), shared conformance cases, and a fake backend;
  `waffle/layout/formats/` the GDS sidecar, stream summary, and artifact publication
  (`docs/layout_backends.md`). `third_party/gdsfactory/` is the first adapter.
* `waffle/artifacts/` immutable artifact store, attempts, and provenance (`docs/artifacts.md`);
  its record types are in `waffle/contracts/results/`.
* `waffle/contracts/experiments/` exact experiment binding records;
  `waffle/experiments/bindings/` selection, snapshot publication, and replay
  (`docs/experiment_bindings.md`). `waffle/experiments/measurements/` imports and
  processes bounded optical-power data using the dataset contracts in
  `waffle/contracts/results/dataset.py` (`docs/measurements.md`).
* `waffle/contracts/analysis/` device-analysis requests; `waffle/simulation/common/backend.py`
  defines the solver interface and `waffle/simulation/photonics/device_analysis/analysis.py`
  handles preflight, execution, and extraction (`docs/device_analysis.md`).
* `third_party/<backend>/` adapters and pinned requirements for gdsfactory and beamz.
  The BeamZ worker uses the solver-neutral protocol and controller in `waffle/execution/`
  (`docs/beamz_execution.md`). Numerical characterization lives in
  `waffle/simulation/photonics/characterization/`, comparisons and calibration in
  `waffle/characterization/` (`docs/waveguide_characterization.md`, `docs/calibration.md`).
* `distributions/` separately packaged demo PDK and examples. The fast suite uses
  small synthetic fixtures within core. `docs/designer_workflow.md` covers installed use.
* `apps/cli/` the `waffle` CLI. `tools/` build macros. `tools/visibility/` package groups.
* Create a planned module only when you implement it; no empty directories.

## Rules

* Every `//waffle` library uses `waffle_py_library` and sets `visibility` to one group from
  `//tools/visibility` (never `//visibility:public`). Core code never imports gdsfactory
  or beamz and never depends on `//third_party/...`.
* Python 3.13, type hints on everything, `from __future__ import annotations`.
  Return `Diagnostic`s for problems the caller can act on; raise for programmer errors.
* Tests are colocated pytest files, one `waffle_py_test` line each.
* pydantic `pattern=` is a search, not a full match: always anchor patterns with `^...$`.

## Commit messages

Use Conventional Commits 1.0.0 for every commit. The subject follows
`type[(scope)][!]: description`, for example `feat(cli): add waveguide replay`.
Use `feat` for new features, `fix` for bug fixes, and types such as `docs`,
`test`, or `refactor` for other changes. Mark breaking changes with `!` before
the colon or a `BREAKING CHANGE: <description>` footer.
[Conventional Commits specification](https://www.conventionalcommits.org/en/v1.0.0/)

## Add a module

1. Create `waffle/<area>/<module>/` with `foo.py`, `foo_test.py`, and `BUILD.bazel`:

   ```starlark
   load("//tools:waffle.bzl", "waffle_py_library", "waffle_py_test")

   waffle_py_library(
       name = "foo",
       srcs = ["foo.py"],
       visibility = ["//tools/visibility:contracts_consumers"],  # pick the layer's group
       deps = ["//waffle/contracts/foundation:canonical"],
   )

   waffle_py_test(name = "foo_test", deps = [":foo"])
   ```

2. Test imports use absolute paths (`from waffle.area.module import foo`).
3. A new test in an existing package is just the `waffle_py_test` line.

## Dependencies

Edit `requirements/core.in` (or a backend's `requirements.in`), run `tools/lock.sh`,
commit the lock files and `MODULE.bazel.lock`. Never edit a lock by hand.

## Commands

```sh
bazel test //...                                   # fast suite (no backends)
bazel build //...
bazel test //third_party/gdsfactory:smoke_test //third_party/beamz:smoke_test   # backends
bazel run //apps/cli:waffle -- doctor
tools/lock.sh
```
