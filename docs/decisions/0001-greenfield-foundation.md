# 0001. Greenfield foundation

Status: accepted (2026-11-16). Covers MOD-001 and MOD-002.

## Context

The repository started empty. There is no prototype to reconcile, so MOD-001 reduces to
this record, `docs/plan/task_graph.md`, and the runnable acceptance commands in
`CONTRIBUTING.md`. Compatibility with earlier entry points is not required.

## Decisions

**Build.** Bazel 9.3.2 (`.bazelversion`) with bzlmod and a committed `MODULE.bazel.lock`.
Modules: `rules_python` 2.5.1, `bazel_skylib` 1.9.2. Native Python rules no longer exist
in Bazel 9; all rules are loaded from `@rules_python`. `explicit_init_py` is enabled, so
runfiles do not get synthesized `__init__.py` files; namespace directories stay PEP 420.

**Python.** Hermetic CPython 3.13 from `rules_python` (3.13.18 at the time of writing).
No obstacle forced a fallback to 3.12. `requires-python` is `>=3.12`.

**Dependencies.** Locks come from `uv pip compile --universal --generate-hashes`
(`tools/lock.sh`) and are consumed by three separate `pip.parse` hubs:

| Hub | Input | Lock |
|---|---|---|
| `@pip_core` | `requirements/core.in` | `requirements/core.lock` |
| `@pip_gdsfactory` | `third_party/gdsfactory/requirements.in` | `.../requirements.lock` |
| `@pip_beamz` | `third_party/beamz/requirements.in` | `.../requirements.lock` |

Backend locks are compiled with `-c requirements/core.lock`, so packages shared with
core (pydantic, pytest) resolve to the same version in every hub. This forced
`pydantic<2.14` in core, because gdsfactory 9.51.0 pins kfactory 3.0.4 which requires
pydantic below 2.14. Backend hubs include their own pytest so a backend test never mixes
two hubs' packages on one `sys.path`. numpy is not in the core hub: nothing in core
uses it yet. Add it when a contract needs arrays.

**Naming.** Distribution `waffle-eda`; import package `waffle` (directory `waffle/`).
The CLI source lives in `apps/cli/` as the plan prescribes and is installed as
`waffle.apps.cli` (hatchling `force-include`); the console script is `waffle`.
Inside Bazel the same file is imported as `apps.cli.main`, so app code must not import
its own siblings by absolute path.

**Tests.** pytest files colocated with their module (`foo.py`, `foo_test.py`), each
registered with `waffle_py_test` from `tools/waffle.bzl`; one line per test.

**Boundaries.** Package-group visibility per layer (`//tools/visibility`) plus
`waffle_py_library`, which adds a `<name>_boundary_test` failing if the library's
transitive deps include `//third_party/...` or a backend pip hub. Pip hub targets are
public and cannot carry visibility, so the boundary test is what covers them.
Details in `ARCHITECTURE.md`.

**Optional backends.** Smoke tests under `//third_party/<backend>` are tagged `backend`
and `manual`: `//...` skips them, and they run only when named. The `upstream` alias
that exposes each pip hub is also `manual`, so `bazel build //...` never fetches a
backend wheel.

**Execution environment.** macOS arm64 with Python 3.13 is the verified target.
Linux is intended: the locks are universal, but nothing has been run there yet.

## Consequences

* Adding a core dependency means editing `requirements/core.in` and re-running
  `tools/lock.sh`, which may re-resolve backend locks.
* Backend versions are pinned exactly in each `requirements.in`; the smoke test reads that
  pin and fails if the installed version differs.
* `pyproject.toml` lists CLI files explicitly (hatchling ignores `exclude` for
  force-included paths); adding an app file means updating it.
