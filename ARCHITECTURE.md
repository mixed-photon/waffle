# Architecture

Waffle has contracts, core services, simulation, adapters, and applications. The rules
below come from the dependency rules in `docs/plan/implementation_plan.md`; this file
records how Bazel enforces them.

## Layers and allowed dependencies

| Layer | Packages | May depend on |
|---|---|---|
| Foundation and contracts | `//waffle/contracts/...` | contracts only (foundation: serialization, identity, diagnostics) |
| Core services | `//waffle/...` except contracts and simulation | contracts, core |
| Simulation | `//waffle/simulation/...` | contracts, core, simulation |
| Adapters | `//third_party/...` | contracts, backend-interface targets, adapters, their upstream pip hub |
| Applications | `//apps/...` | everything, including adapters (assembly selects backends) |
| Validation | `//validation/...` | everything |

Rule 4 of the plan (native numerics have limited dependencies, optional Waffle bindings)
applies to `//waffle/simulation/engines/...` once it exists; it needs no extra group yet.

## How it is enforced

1. **Package groups** in `//tools/visibility`. `layer_*` groups list a layer's packages;
   `*_consumers` groups list who may depend on it, and targets use them as `visibility`:

   | Consumer group | Allowed users |
   |---|---|
   | `contracts_consumers` | all layers |
   | `core_consumers` | core, simulation, apps, validation |
   | `simulation_consumers` | simulation, apps, validation |
   | `backend_interface_consumers` | core, simulation, adapters, apps, validation |
   | `third_party_consumers` | adapters, apps, validation |
   | `apps_consumers` | apps, validation |

   Targets are private unless they set `visibility`. Only API targets are given a group.
   Contracts and core cannot depend on `//third_party/...` or on `//apps/...`.

2. **Boundary test.** Pip hub targets are public, so visibility cannot stop
   `@pip_gdsfactory//gdsfactory` appearing in a core `deps`. `waffle_py_library`
   (`tools/waffle.bzl`) therefore creates `<name>_boundary_test`, which walks the library's
   transitive `deps` with an aspect (`tools/boundary.bzl`) and fails if it finds a target
   under `//third_party` or in the `pip_gdsfactory` / `pip_beamz` repositories. All
   `//waffle` libraries must use `waffle_py_library`. Backends are reachable only through
   `//third_party/<backend>:upstream`.

3. **Environment test.** `//tools:backend_isolation_test` asserts neither backend is
   importable in the default test environment.

4. **Default run.** Backend tests are `manual`, so `bazel test //...` neither fetches nor
   imports a backend.

### Verified rejections

Adding `deps = ["//third_party/gdsfactory:upstream"]` to
`//waffle/contracts/foundation:canonical` fails at analysis:

```
ERROR: .../waffle/contracts/foundation/BUILD.bazel:5:17: in py_library rule
//waffle/contracts/foundation:canonical: Visibility error:
alias '//third_party/gdsfactory:upstream' referring to target ... is not visible from ...
```

Adding the public pip targets directly (`@pip_gdsfactory//gdsfactory`,
`@pip_beamz//beamz`) builds, but the boundary test fails:

```
FAIL: //waffle/contracts/foundation:canonical_boundary_test
Forbidden dependencies of @@//waffle/contracts/foundation:canonical:
  @pip_beamz (48 targets)
  @pip_gdsfactory (159 targets)
```

## Python packages

`waffle/` is the import package. Directories without code (for example `waffle/contracts`)
carry no `__init__.py`; they are namespace packages.

## Tests

Unit tests sit next to their module. Cross-component workflows go in `//validation/...`
with their own fixtures; that directory is created with its first real test.
Reusable backend conformance cases live with the contract they exercise.
