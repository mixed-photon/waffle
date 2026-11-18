# Experiment bindings and replay

`resolve_binding` accepts a circuit, exact revision keyed design and interface chains,
candidate model revisions, view mappings, operating conditions, and requested optical
response pairs for each instance. A caller may select a model revision explicitly for
each instance. Without a selection, the resolver accepts only one compatible model;
multiple compatible models produce `binding.ambiguous_model`. Each
`CandidateDecision` records the preflight diagnostics that explain acceptance or
rejection. Two instances of the same design may pin different model revisions.

An `ExperimentBinding` stores the exact circuit revision and, for each instance,
design, implementation, component, interface, model, mapping, analysis, effective
operating conditions, requested channel subset, and evaluator name and version or
artifact payload reference. The payload forms are exclusive. This is a selection
record; it does not claim to solve the circuit.

`publish_binding` writes the binding and required record snapshots into one immutable
`experiment_binding` artifact. Publication checks every referenced record's content
hash and dependency chain, validates the mapping against its interface, layout, and
physical problem, rechecks model applicability under the recorded conditions, and
verifies referenced artifacts. The snapshot set includes pinned layout, physical
problem, and technology records, plus supported revision-valued model evidence.
An unsupported evidence record kind fails with a diagnostic. Artifact dependencies
include model payloads, artifact-valued evidence, and imported layout sources.

`replay_binding` loads only those snapshots and pins. It never queries current model
candidates or silently upgrades a historical record under the same hash. A missing
snapshot, changed record hash, missing artifact, or corrupt artifact returns a
structured diagnostic. The returned binding and records are the inputs for a later
analysis executor; replay does not itself run a model or a circuit solver.
