# Dependency invalidation and recovery

Exact replay and numerical equivalence have separate identities. `revision_of(record)`
hashes the entire immutable record, including descriptive metadata. Historical
experiment bindings publish complete exact record snapshots and replay those pins;
new model coefficients or technology revisions never rewrite a previous experiment.

`waffle.simulation.common.run_fingerprint.run_fingerprint` describes the dependencies
of a bounded electromagnetic execution. It requires an analysis request that pins
the supplied physical problem, an operation version, and a discovered effective
worker environment. Its result contains the exact request/problem references,
an aggregate digest, and named component hashes. `compare_run_fingerprints`
returns numerical equivalence, exact replay identity, changed component names and
a readable reason. All named components and versioning are explicit:

| Component | Included |
| --- | --- |
| physics | `physical_problem_fingerprint`: coordinate dbu, domain, boundaries, geometry, material/index values, ports, approximations |
| numerical | mesh spacing, time step, step count |
| excitations | kind, location/port, wavelength, mode |
| observables | intrinsic observable kinds, field components, channels/source indices |
| backend | backend name, version, settings schema and all parameters |
| environment | every supplied effective worker interpreter/library/adapter/platform/precision/device entry |
| operation | electromagnetic execution operation version |
| mapping | exact optional mapping revision (conservative) |
| resources | resource and wall-time limits (conservative completeness constraint) |
| polarization/schema | formulation polarization, request schema and fingerprint algorithm version |

Worker environment means the process that actually computes the response, including
its effective versions and numerical device/precision settings. A launcher path or
the controller's Python environment is insufficient. Callers must discover and
supply that information before comparing keys; empty environments are rejected.
The API does not promise equivalence across unknown numerical environment changes.

Physical metadata, entity names, source layout/design/technology references and
material citations do not affect the physics key. Such edits still change exact
replay revisions. Geometry, material values, coordinate grid and reduction changes
affect the physics key. Model coefficients do not affect a run of fixed geometry
and materials: they are inputs to a different operation.
`model_evaluation_fingerprint` conservatively includes model coefficients, evaluator
pin, all model claims/metadata/evidence, operating values and the mapping. Model
metadata can contain executable evaluator inputs, so it is deliberately retained.
Neither fingerprint substitutes for applicability or completeness validation.

There is no automatic cache reuse or hydration. An equal digest only means the
recorded numerical dependencies match. No candidate artifact is surfaced as reusable,
and no incomplete, corrupt or merely metadata-matching result can become a completed
run through this API. A future cache must verify completed attempt state, all payload
digests, output schema, coverage and exact record association before returning data.

## Recovery behavior

The local store atomically publishes complete immutable directories on a single
POSIX filesystem. Concurrent identical publications converge on one artifact, with
separate attempt/provenance records for every producer. Publication does not erase
attempt history. Uncommitted staging remains partial output and never appears in the
artifact catalog. Completed artifact reads check payload integrity.

An abruptly terminated worker can leave a `started` attempt without an outcome.
This means interrupted or still running; recovery does not infer liveness or mark it
successful. A retry starts a new attempt and retains the prior staging and identity.
The isolated execution controller also uses distinct attempt directories, records
worker failures/cancellation and preserves prior completed artifacts on retries.
A killed worker is restarted as a fresh attempt; numerical checkpoint resume is not
implemented.

## Acceptance commands

```sh
bazel test //validation/recovery:recovery_test //waffle/execution:worker_test
bazel test //validation/demo_distribution:reference_test
```

On 2026-11-17 the recovery target passed five fast tests: inspectable dependency
changes, changed geometry/material/grid, sixteen concurrent publications, a real
subprocess that stages output then exits with `os._exit(17)`, and historical binding
publication/replay after coefficient changes. The abrupt-death test verifies no
partial artifact is visible and a new successful attempt preserves the original
started record. These tests use synthetic fixture technology and no optional backend.
The explicit demo target verifies the corrected scalar Hz lateral TM continuity,
analytic phase and reciprocal/unitary matrices, GDS round trip and binding replay.
