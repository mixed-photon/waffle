# Waffle

Versioned component and model foundation for photonic design: device definitions,
theoretical models, layout, physical simulation, compact models, and measurements.
GDSFactory and BeamZ are optional backends behind Waffle-owned interfaces.

The local reference workflow runs a waveguide from GDSFactory layout through
isolated BeamZ simulation and exact experiment replay. It publishes a qualified
two-port scattering matrix at 1550 nm TM and offers a separate forward-sample
workflow for synthetic power calibration. The MMI characterization publishes
two qualified forward responses. The demo PDK and analytic examples install as
separate wheels. See the [installed workflow](docs/designer_workflow.md),
[lifecycle evidence](docs/lifecycle_report.md), and [milestone status](docs/plan/progress.md).

```sh
bazel test //...                      # fast tests, no optional backends
bazel run //apps/cli:waffle -- doctor  # environment preflight
```

See `CONTRIBUTING.md` for setup, `ARCHITECTURE.md` for layering rules, and
`AGENTS.md` if you are a coding agent.
