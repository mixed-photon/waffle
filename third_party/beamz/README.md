# beamz

Bounded 2D FDTD adapter and isolated worker (MOD-012).

* Upstream: <https://pypi.org/project/beamz/>, Apache-2.0 license (shipped `LICENSE`).
  Development status: Alpha.
* Pinned: `beamz==0.6.2`, with jax/jaxlib 0.12.0.1, numpy 2.5.5, scipy 1.18.1 from
  `requirements.lock`. Upstream supports Python `>=3.11,<3.15`.
* Hub: `@pip_beamz`. Reach it only through `//third_party/beamz:upstream`.
* Runtime: JAX runs on CPU on macOS arm64; no GPU configuration has been tried.
* Smoke test: `bazel test //third_party/beamz:smoke_test` (tags `backend`, `manual`).
  Importing only; it does not run a simulation.
* CPU propagation and raw-monitor probe: `bazel test
  //third_party/beamz:capability_spike_test --test_output=all --nocache_test_results
  --test_arg=-s` (also `backend`, `manual`). Findings and limits:
  `docs/beamz_capabilities.md`.
* Real adapter + isolated worker acceptance: `bazel test
  //third_party/beamz:adapter_test --test_output=all --test_arg=-s`.
  Supported inputs, public Python/process APIs, persistence, numerical evidence and
  limitations: `docs/beamz_execution.md`. `//third_party/beamz:worker` is the
  separate worker launcher; installed entry point: `python -m third_party.beamz.worker`.
* To change the pin, edit `requirements.in` and run `tools/lock.sh`.
