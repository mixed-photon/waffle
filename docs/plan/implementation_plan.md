# Waffle Component, Model, and Simulation Foundation — Implementation Plan

> **Greenfield amendment (owner decision, 2026-11-16).** This repository started empty.
> There is no prototype to reconcile: do not read, copy, or reference any other Waffle
> checkout. Wherever this plan says "existing", "refine", "migrate", or "prototype",
> read it as "implement from scratch". MOD-001 reduces to a decision record, a task
> dependency graph, and runnable acceptance commands. Compatibility with earlier
> entry points or records is not required.

## Objective

Build a versioned component and model foundation connecting device definitions, theoretical models, layout, physical simulation, compact models, and experimental observations.

Deliver a complete photonic reference workflow using GDSFactory and BeamZ behind Waffle-owned intermediate representations and backend interfaces. Establish the boundaries needed to introduce native layout, geometry, FDTD, and other numerical implementations independently.

Support a practical local deployment for a small design team, with storage and execution contracts that can later support larger organizations.

This revision uses **Waffle** as the project name. The repository structure is a target to reconcile with the existing implementation during the first task.

## Required outcomes

A developer or designer can:

1. Define a component interface and parameterized physical implementation.
2. Resolve a design against a pinned technology revision.
3. Generate Layout IR through GDSFactory.
4. Export GDS with traceable layout and port metadata.
5. Bind an analytic model to a circuit instance.
6. Convert the layout and process stack into a physical simulation request.
7. Execute the request through BeamZ.
8. Extract and validate a bounded frequency-domain model.
9. Publish the model with its evidence and applicability.
10. Select different models for different experiments or instances.
11. Import measurement data associated with a fabricated specimen.
12. Reproduce an experiment using its recorded dependencies.

An installed release must expose this workflow through Python and a headless entry point. Contributors use Bazel to build, test, and package the implementation.

## Architectural decisions

### Build and dependency management

Use Bazel targets to define buildable units, enforce dependency boundaries, and produce release artifacts.

- Pin external dependencies through the chosen Bazel dependency mechanisms.
- Define target visibility explicitly.
- Register colocated tests as Bazel test targets.
- Keep heavyweight backends optional.
- Provide standalone build and release targets for engines that require independent use.
- Record runtime environments and effective solver versions with simulation results.

Separate Python packages are appropriate where distribution or dependency isolation requires them. Source directories do not automatically imply separately published packages.

### External dependencies

Use `third_party/` to own external dependency integration.

Each integration may contain:

- Dependency declarations or references to root dependency declarations.
- Bazel build targets.
- Waffle adapter code.
- Worker entry points.
- Compatibility and conformance tests.
- Upstream license information and necessary patches.
- Runtime configuration documentation.

Bazel-managed upstream sources and installed distributions can remain in external dependency storage.

### Tests

Colocate tests with their owning module, adapter, or engine.

Cross-component workflows belong in dedicated validation packages with their own tests and fixtures. Reusable backend conformance cases belong with the corresponding public contract or backend interface.

### PDKs and examples

Distribute PDK content and user-facing examples through independently permissioned repositories or packages.

The core repository owns PDK loading, authoring, validation, and export infrastructure. Small redistributable synthetic fixtures may remain beside core tests.

Each external PDK or example declares compatible Waffle versions and exact dependencies for reproducible runs.

### Simulation and engines

Simulation modules describe analyses, prepare physical problems, and interpret results.

Numerical engines execute particular numerical formulations.

- Photonic device analysis prepares optical device experiments.
- Photonic circuit analysis evaluates interconnected compact models.
- Thermal analysis prepares heat-transfer problems.
- Electronics analysis prepares electrical circuit or electromagnetic problems.
- Multiphysics coordination exchanges quantities between coupled analyses.
- Native engines implement field updates, equation assembly, circuit solving, and related numerical operations.

An electromagnetic FDTD engine can serve both optical and RF analysis when its capabilities cover the requested problem.

## Target repository structure

```text
waffle/
├── MODULE.bazel
├── MODULE.bazel.lock
├── .bazelversion
├── ARCHITECTURE.md
├── AGENTS.md
│
├── waffle/
│   ├── contracts/
│   │   ├── component/
│   │   ├── geometry/
│   │   ├── layout/
│   │   ├── physics/
│   │   ├── models/
│   │   └── results/
│   ├── design/
│   │   ├── components/
│   │   ├── instances/
│   │   ├── mappings/
│   │   └── transactions/
│   ├── geometry/
│   │   ├── services/
│   │   └── kernel/                 # Native implementation as developed
│   ├── layout/
│   │   ├── services/
│   │   ├── database/               # Native implementation as developed
│   │   └── formats/                # GDS/OASIS readers and writers
│   ├── technology/                 # PDK consumption and resolution
│   ├── pdk/                        # PDK authoring, validation, export
│   ├── models/
│   ├── characterization/           # Shared fitting/publication infrastructure
│   ├── experiments/
│   ├── artifacts/
│   ├── workflows/
│   ├── execution/
│   ├── statistics/
│   │
│   ├── simulation/
│   │   ├── common/
│   │   ├── photonics/
│   │   │   ├── device_analysis/
│   │   │   ├── circuit_analysis/
│   │   │   └── characterization/   # Optical recipes and extraction
│   │   ├── electronics/
│   │   │   ├── circuit_analysis/
│   │   │   └── electromagnetic/
│   │   ├── thermal/
│   │   ├── multiphysics/
│   │   └── engines/
│   │       ├── electromagnetic/
│   │       │   ├── fdtd/
│   │       │   ├── fem/
│   │       │   ├── eme/
│   │       │   └── modes/
│   │       ├── thermal/
│   │       ├── electrical/
│   │       └── optical/
│   │
│   └── numerics/                   # Shared utilities when reuse is demonstrated
│
├── third_party/
│   ├── gdsfactory/
│   ├── beamz/
│   ├── ngspice/
│   └── iic_osic_tools/
├── environments/
├── apps/
│   ├── cli/
│   └── gui/
├── validation/
│   └── component_model_lifecycle/
├── benchmarks/
├── tools/
└── docs/
```

Each implemented module has its own `BUILD.bazel` and colocated tests. Create planned modules when their implementation begins.

External repositories may include:

```text
waffle-pdk-demo/
waffle-pdk-<foundry>/
waffle-example-waveguide/
waffle-example-mmi/
waffle-example-electrical-inverter/
waffle-project-<restricted-design>/
```

## Dependency rules

1. Contracts depend on a small foundation of serialization, units, and identity utilities.
2. Core domain services depend on contracts and backend interfaces.
3. External adapters depend on those interfaces and their upstream libraries.
4. Native numerical implementations have limited dependencies and expose optional Waffle backend bindings.
5. Application assembly selects and registers concrete backends.
6. PDK packages and examples consume published Waffle interfaces.
7. Cross-domain coupling is coordinated explicitly through multiphysics workflows.
8. Build visibility and conformance tests enforce these boundaries.

## Core records

| Record | Responsibility |
|---|---|
| Component definition | Stable identity, terminal interface, design parameter schema |
| Physical implementation | Generator or imported layout, technology requirements |
| Resolved design | Concrete parameters and pinned implementation dependencies |
| Circuit instance | Instance identity, placement, connectivity, design reference |
| Layout artifact | Geometry, hierarchy, layers, ports, coordinate conventions |
| Physical problem | Material distribution, physical domain, boundary conditions |
| Analysis request | Excitations, observables, conditions, numerical requirements |
| Model revision | Evaluator or payload, capabilities, applicability, evidence |
| Specimen | Fabricated-design reference and physical sample identity |
| Dataset | Observations, axes, units, completeness, provenance |
| Validation report | Reference, metrics, tolerances, tested coverage, limitations |
| Experiment binding | Exact selected models and dependencies for each instance |

---

## Phase 0 — Reconcile the existing prototype

### MOD-001 — Audit, preserve, and map existing capabilities

**Dependencies** — None.

Inspect the current repository and map existing implementations to this plan.

Include the reported device versioning, resistor PDK, Monte Carlo checkpointing, Layout/EM IRs, GDSFactory adapter, BeamZ worker, model publication, and waveguide extraction workflow.

Record:

- Implemented behavior and existing tests.
- Contract differences and migration needs.
- Reusable numerical and storage functionality.
- Target ownership and Bazel visibility.
- Supported execution environments.
- Numerical assumptions in existing demonstrations.

**Acceptance**

- Every existing public entry point has a documented disposition.
- Reusable functionality is retained.
- Architectural changes have explicit migration steps.
- The first milestone has runnable acceptance commands.

**Deliverables** — Decision record, implementation inventory, migration map, and task dependency graph.

### MOD-002 — Establish Bazel boundaries and colocated tests

**Dependencies** — MOD-001.

Define public API targets, implementation targets, adapter targets, and test targets.

Move tests alongside their owners where needed. Establish dedicated cross-component validation targets.

Move external bindings into `third_party/` with compatibility handling for existing entry points.

**Acceptance**

- Core tests run without loading optional backends.
- Visibility prevents core services from depending directly on external solver implementations.
- Real-backend tests have explicit runnable targets.
- Adapter tests are colocated.
- Required existing commands remain usable through the migration.

**Deliverables** — Build rules, dependency policy, test layout, and developer instructions.

## Phase 1 — Identity, geometry, and layout

### MOD-003 — Implement component and model identity contracts

**Dependencies** — MOD-001.

Implement or refine component definitions, implementations, resolved designs, instances, specimens, and model revisions.

Separate design parameters, operating conditions, and fitted coefficients.

Define schema versions, immutable published revisions, and migration behavior.

**Acceptance**

- Multiple instances can share one resolved design.
- Multiple specimens can reference one fabricated revision.
- Exact dependency revisions survive serialization.
- Invalid units and missing parameters produce structured diagnostics.
- Existing supported records remain readable or have a documented migration.

### MOD-004 — Define Geometry IR and Layout IR

**Dependencies** — MOD-003.

Specify the initial geometry and layout subsets.

Geometry covers coordinates, shapes, curves where supported, and transforms.

Layout covers cells, hierarchy, instances, layers, physical ports, and geometry references.

Define database units, precision, grid handling, stable identifiers, and unsupported-construct diagnostics.

**Acceptance**

- Hierarchical layouts round-trip through serialization.
- Transform composition behaves consistently.
- Unit conversions preserve the declared precision.
- Port identities survive layout operations.
- Unsupported shapes or hierarchy constructs fail explicitly.

### MOD-005 — Implement the GDSFactory layout backend

**Dependencies** — MOD-002 and MOD-004.

Implement the external adapter under `third_party/gdsfactory/`.

Support the selected generator subset and conversion into Layout IR. Implement GDS export with traceable metadata.

Define separate backend capabilities for generation, import, export, and geometry operations.

**Acceptance**

- Generate a reference waveguide and MMI.
- Preserve supported hierarchy, layer mappings, ports, and transforms.
- Export and re-import representative geometry for semantic comparison.
- Verify sidecar association with exported GDS.
- Exercise the adapter from its declared runtime.

### MOD-006 — Implement technology resolution and layout-to-physics conversion

**Dependencies** — MOD-003 and MOD-004.

Resolve a technology revision containing the supported process stack and material models.

Convert Layout IR into the physical geometry and materials needed by the selected simulation.

Track approximations such as effective-index reduction explicitly.

**Acceptance**

- Validate extrusion dimensions, material assignments, and port placement.
- Preserve references to the source layout and technology.
- Diagnose missing or ambiguous layer mappings.
- Distinguish 2D approximations from 3D physical descriptions.
- Relevant process changes produce different resolved requests.

### MOD-007 — Implement view and channel mappings

**Dependencies** — MOD-004 and MOD-006.

Map logical terminals to layout ports, simulation ports/modes, model channels, and measurement channels.

Record transforms, permutations, reference planes, and normalization conventions.

**Acceptance**

- Detect port swaps and incomplete mappings.
- Preserve connectivity through supported rotations and reflections.
- Identify uncomputed response channels.
- Validate mappings against referenced interface revisions.
- Report unsupported modal transformations explicitly.

## Phase 2 — Storage and model selection

### MOD-008 — Implement immutable artifacts and provenance

**Dependencies** — MOD-003.

Provide a local artifact store through an interface suitable for later remote implementations.

Support content integrity, atomic publication, partial attempts, retries, and typed provenance relationships.

**Acceptance**

- Failed publication cannot expose incomplete output as complete.
- Retrying publication is idempotent.
- Metadata inspection avoids loading large arrays.
- Corruption is detected.
- Published models and datasets are traceable to their inputs and producing operations.

### MOD-009 — Implement capability and applicability checks

**Dependencies** — MOD-003 and MOD-007.

Implement the first executable model capability for optical frequency-domain scattering.

Define applicability to interfaces, implementations, design parameters, operating conditions, and sampled domains.

Represent unknown coverage and uncertainty explicitly.

**Acceptance**

- Reject incompatible analyses and interfaces.
- Distinguish sampled coverage from validated coverage.
- Apply a declared extrapolation policy.
- Preserve partial observation coverage.
- Demonstrate shared metadata with an electrical model descriptor.

### MOD-010 — Implement experiment bindings and replay

**Dependencies** — MOD-008 and MOD-009.

Resolve model choices per instance and persist exact experiment dependencies.

Support explicit selection and a documented deterministic selection policy where automatic selection is provided.

**Acceptance**

- Identical component designs can use different model revisions in different instances.
- Resolution explains accepted and rejected candidates.
- Ambiguous selection produces an actionable diagnostic.
- Replay uses recorded dependencies.
- Missing historical artifacts are reported clearly.

## Phase 3 — Photonic analysis and BeamZ

### MOD-011 — Implement device-analysis and backend interfaces

**Dependencies** — MOD-006, MOD-007, and MOD-009.

Implement the photonic device-analysis layer and shared electromagnetic backend interface.

Separate physical preparation, execution, and response extraction.

Specify capability queries, progress, cancellation, resource requests, and result completeness.

**Acceptance**

- Device-analysis code operates through the backend interface.
- Backend-specific settings are explicit and recorded.
- A backend can report raw fields or supported normalized observables.
- Extraction conventions are validated at the interface.
- A test backend exercises orchestration without expensive simulation.

### MOD-012 — Integrate BeamZ execution

**Dependencies** — MOD-002, MOD-008, and MOD-011.

Refine or implement the BeamZ adapter and isolated worker under `third_party/beamz/`.

Preserve attempt history and atomic completion. Record effective inputs, versions, environment, and diagnostics.

**Acceptance**

- Run a supported waveguide field simulation.
- Validate units, geometry orientation, and expected propagation behavior.
- Reject unsupported requests before launch where possible.
- Cancellation and failure leave inspectable attempt records.
- Retry does not overwrite completed results.
- Real-backend tests run through documented targets.

### MOD-013 — Implement reference components and analytic models

**Dependencies** — MOD-005, MOD-009, and MOD-010.

Create the public reference waveguide and MMI definitions in the independently distributed demonstration library or PDK.

Document analytic assumptions, phase behavior, reflection treatment, and validity.

**Acceptance**

- Generate Layout IR and GDS.
- Evaluate analytic responses through public APIs.
- Verify applicable physical consistency claims.
- Bind models in an optical circuit experiment.
- Run the installed example outside the core checkout.

### MOD-014 — Extract and validate simulation-derived models

**Dependencies** — MOD-012 and MOD-013.

Begin with the bounded waveguide workflow, then extend to the supported MMI case.

Version normalization, reference calculations, extraction, interpolation, and validation procedures.

**Acceptance**

- Identify observed and uncomputed scattering elements.
- Full-matrix claims have sufficient independent excitation coverage.
- Check magnitude and phase.
- Assess numerical convergence separately from interpolation error.
- Use held-out samples when claiming interpolation coverage.
- Publish applicability and validation reports.
- Expose the resulting model through the existing experiment binding mechanism.

## Phase 4 — Measurements and calibration

### MOD-015 — Implement measurement ingestion

**Dependencies** — MOD-007 and MOD-008.

Support a documented import format and a clearly labeled synthetic fixture.

Capture specimen identity, fabricated design, observables, units, conditions, calibration, channel mapping, and uncertainty metadata.

**Acceptance**

- Raw observations remain traceable.
- Processing creates new artifacts.
- Power-only data retains its limited observable scope.
- Unknown uncertainty differs from zero uncertainty.
- Import does not implicitly qualify a model.

### MOD-016 — Implement bounded calibration and validation

**Dependencies** — MOD-014 and MOD-015.

Fit a small set of identifiable parameters.

Record prior assumptions, simulated contributions, fitting data, validation data, and algorithm settings.

**Acceptance**

- Unsupported inference is diagnosed.
- Fitting and validation data are distinguishable.
- Publication creates a new model revision.
- Provenance includes all contributing datasets.
- Comparison reports state the tested quantities and conditions.

## Phase 5 — Small-team release

### MOD-017 — Separate PDK and example distributions

**Dependencies** — MOD-013.

Create independently distributable demonstration PDK and example packages or repositories.

Provide dependency pins, installation instructions, licenses, and validation targets.

**Acceptance**

- Access and distribution boundaries are explicit.
- Examples consume released Waffle APIs.
- Installation works outside the core checkout.
- Restricted PDK content is supplied through the same interfaces.
- Core tests use redistributable fixtures.

### MOD-018 — Implement dependency invalidation and recovery tests

**Dependencies** — MOD-010, MOD-014, and MOD-016.

Define operation-specific dependency fingerprints.

Exercise changes to geometry, materials, numerical settings, coefficients, and descriptive metadata.

**Acceptance**

- Physically relevant changes invalidate affected results.
- Historical experiments retain their recorded dependencies.
- Concurrent attempts publish consistent state.
- Interrupted work resumes or retries predictably.
- Reuse decisions are inspectable.

### MOD-019 — Package the local designer workflow

**Dependencies** — MOD-017 and MOD-018.

Deliver an installed Python API and CLI for the complete lifecycle.

Provide backend environment selection, preflight checks, readable diagnostics, and links or paths to result artifacts.

Bazel remains available for contributors and release engineering.

**Acceptance**

- A clean supported installation completes the reference workflow.
- Users can select analytic or simulated models.
- Errors identify affected instances, ports, or artifacts where possible.
- The release documents supported platforms and resource requirements.
- Numerical validation commands are separated from fast checks.

### MOD-020 — Deliver the lifecycle report and developer handoff

**Dependencies** — MOD-019.

Produce a final report containing resolved identities, model choices, evidence, validation coverage, limitations, and replay instructions.

Document how to add another component, PDK, and backend.

**Acceptance**

- Every required outcome is demonstrated.
- Existing supported prototype behavior has been accounted for.
- Implementation, tests, recorded numerical evidence, and documentation are reviewable.
- Remaining limitations are explicit.

## Hierarchical geometry extension

### MOD-021 — Hierarchical 2D layout and 3D physical geometry

**Dependencies** — MOD-005 and MOD-006.

Extend layout interchange to compact repeated-cell arrays and add window selection
through the hierarchy. Use the existing layout backend for the first implementation
and measure extraction before choosing native geometry work.

Construct localized 3D material geometry from a pinned layout and process stack,
and support directly authored 3D shapes with explicit placements, materials, and
overlap rules. Use the physical geometry for consistent sections and local
simulation preparation.

**Acceptance**

- Round-trip supported arrays through GDS with stable member paths and port placement.
- Match window selection against a fully expanded reference and measure work on a
  large repeated-cell layout at fixed window size.
- Reproduce dimensions and material boundaries for a multilayer via-and-trench
  fixture in the 3D region and its cross-section.
- Compare localized physical construction with the same region cut after building
  the full fixture, including features whose sidewalls enter the query window.

## Native implementation track

Start native implementation work against the interfaces established above.

### NAT-001 — Native layout and geometry

Deliver capabilities incrementally, beginning with a measured bottleneck.

Possible increments include native GDS output, layout storage, geometry queries, and selected Boolean operations.

**Acceptance**

- Run the same applicable layout conformance cases as the external backend.
- Compare semantic outputs.
- Measure runtime and memory on representative workloads.
- Preserve declared precision and port mappings.

### NAT-002 — Native electromagnetic FDTD

Place the implementation under:

```text
waffle/simulation/engines/electromagnetic/fdtd/
```

Expose independent solver, test, benchmark, and release targets, plus a Waffle backend binding.

**Acceptance**

- Pass analytical and convergence checks.
- Run the shared waveguide and MMI cases for supported features.
- Compare physical observables using consistent conventions.
- Measure end-to-end time to a defined accuracy tolerance.
- Record peak memory and cold versus repeated execution costs.

Thermal FEM, electromagnetic FEM, EME, and other engines should follow equivalent capability-specific milestones. Shared numerical infrastructure should be extracted when concrete reuse is demonstrated.

## Large-deployment track

These milestones extend the local foundation after the reference workflow is stable.

| Task | Scope | Acceptance focus |
|---|---|---|
| SCALE-001 | Remote execution | Durable jobs, cancellation, worker failure recovery, resource accounting |
| SCALE-002 | Remote artifact storage | Selective access, concurrent publication, retention, integrity |
| SCALE-003 | Access-controlled catalogs | Authorization across discovery, payloads, logs, exports, and workers |
| SCALE-004 | Distributed Monte Carlo | Stable sample identities, correlation scopes, idempotent completion, failure accounting |
| SCALE-005 | PDK and model release governance | Compatibility, qualification coverage, deprecation, reproducible historical releases |
| SCALE-006 | Operational monitoring | Queue latency, utilization, failure rates, convergence failures, storage usage |
| SCALE-007 | Foundry pilot | One bounded technology/model workflow validated against agreed acceptance criteria |

Solver-level multi-GPU scaling belongs to the relevant engine. Campaign-level distribution belongs to workflows and execution.

## Team allocation

For a small development team, assign ownership across four workstreams:

| Workstream | Ownership |
|---|---|
| Contracts and design | Identity, IRs, mappings, compatibility |
| Layout and technology | GDSFactory integration, PDK resolution, physical preparation |
| Models and numerics | Analysis, BeamZ, extraction, validation, calibration |
| Platform and delivery | Bazel, artifacts, execution, packaging, examples |

A fifth contributor can focus on designer workflows and cross-component validation. Roles may overlap, but shared contracts need a named owner.

## Definition of done

The initial foundation is complete when a small team can install a supported release, generate a physical design, characterize it, publish and select models, ingest bounded measurement evidence, and reproduce the resulting experiment.

The implementation must demonstrate explicit identity, view correspondence, capability checks, applicability, immutable provenance, recoverable execution, and independently managed PDK/example dependencies.

Foundry-scale readiness is assessed separately against the chosen production workflow and its numerical, operational, and access requirements.
