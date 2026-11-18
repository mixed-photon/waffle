# Waffle

Versioned component and model foundation for photonic design: device definitions,
theoretical models, layout, physical simulation, compact models, and measurements.
GDSFactory and BeamZ are optional backends behind Waffle-owned interfaces.

The local 0.1.0 release runs a reference waveguide from GDSFactory layout through
isolated BeamZ simulation, a validated forward sample, synthetic power calibration,
and exact experiment replay. The MMI characterization publishes two qualified
forward responses. The demo PDK and analytic examples install as separate
wheels. See the [installed workflow](docs/designer_workflow.md),
[lifecycle evidence](docs/lifecycle_report.md), and [milestone status](docs/plan/progress.md).

```sh
bazel test //...                      # fast tests, no optional backends
bazel run //apps/cli:waffle -- doctor  # environment preflight
```

See `CONTRIBUTING.md` for setup, `ARCHITECTURE.md` for layering rules, and
`AGENTS.md` if you are a coding agent.
