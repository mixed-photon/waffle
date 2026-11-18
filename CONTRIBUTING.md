# Contributing

## Setup

Required: Bazel 9.3.2 (Bazelisk reads `.bazelversion`) and `uv` (only for locks and
wheels). Python is provided hermetically by Bazel; system Python is not used. Verified on
macOS arm64. The installed release workflow was also verified in a Debian
Linux arm64 container. See the [Linux runtime prerequisites](docs/designer_workflow.md).

## Everyday commands

```sh
bazel build //...                  # build everything
bazel test //...                   # fast tests; never fetches or imports gdsfactory/beamz
bazel test //waffle/contracts/foundation:canonical_test    # one test
bazel run //apps/cli:waffle -- doctor                      # environment preflight
```

Local overrides go in `user.bazelrc` (git-ignored).

## Fast tests vs real-backend tests

`bazel test //...` runs unit tests, `*_boundary_test`s, and `//tools:backend_isolation_test`.
Real-backend tests are tagged `backend` and `manual`, so wildcards skip them. The first
run downloads the backend wheels and takes about a minute:

```sh
bazel test //third_party/gdsfactory:smoke_test
bazel test //third_party/beamz:smoke_test
bazel test //third_party/beamz:capability_spike_test  # bounded CPU propagation, raw monitors
bazel test //third_party/beamz:adapter_test          # real isolated worker, units and coordinates
bazel test //validation/beamz_characterization:real_test  # measured forward waveguide sample
bazel test //validation/beamz_characterization:directional_benchmark_test  # ten isolated runs
bazel test //validation/mmi_characterization:real_test   # two forward MMI samples and replay
bazel query 'attr(tags, backend, tests(//...))'    # list all backend tests
```

### Layout backend tests

The gdsfactory adapter (`third_party/gdsfactory/`) has its own targets, all tagged `backend`
and `manual` and collected in one suite. They run the shared conformance cases from
`//waffle/layout/backend:conformance` against the real library:

```sh
bazel test //third_party/gdsfactory:all_backend_tests     # smoke, backend, convert, session
bazel test //third_party/gdsfactory:backend_test --test_output=errors   # acceptance and conformance
bazel query 'attr(tags, backend, tests(//third_party/gdsfactory/...))'
```

The same cases run against the in-memory fake in the fast suite
(`bazel test //waffle/layout/backend:fake_test`). See `docs/layout_backends.md` for adding a
backend.

### Foundation lifecycle checks

The fast suite also exercises view mappings, optical evaluator dispatch, exact
experiment snapshot replay, backend orchestration with a fake solver, and bounded
measurement ingestion. A cross-component check uses actual physical preparation:

```sh
bazel test //validation/component_model_lifecycle:mapping_model_test
```

Its response evaluator is synthetic. It is not numerical validation of a physical
device or a complete designer workflow. See `docs/plan/progress.md` for milestone
status and `docs/beamz_capabilities.md` for the real backend probe's limits.

## Adding code and tests

See `AGENTS.md` ("Add a module"). A test is `foo_test.py` next to `foo.py` plus
`waffle_py_test(name = "foo_test", deps = [":foo"])` in the package's `BUILD.bazel`.

## Dependencies and locks

1. Edit `requirements/core.in`, or `third_party/<backend>/requirements.in`
   (backend packages are pinned with `==`; the smoke test checks the pin).
2. Run `tools/lock.sh`. It resolves core first, then backends constrained by core.
3. Run `bazel test //...` and commit `*.lock` and `MODULE.bazel.lock`.

## Checking the boundaries

```sh
bazel query 'deps(//waffle/...)' | grep -E '^//third_party/|pip_(gdsfactory|beamz)'   # prints nothing
```

The same check runs automatically as each library's `*_boundary_test`.

## Build the local release

```sh
bazel run //tools:release_wheels -- --out dist/wheels
```

This produces the Waffle wheel, synthetic demo PDK, and two independently
installable examples. The [installed workflow](docs/designer_workflow.md) shows
how to install outside the checkout, choose a solver environment, and replay.
