# Artifacts and provenance

`waffle.artifacts` stores immutable results (datasets, models, simulation output) with
integrity checks, attempt history, and provenance. Records live in
`waffle/contracts/results/`; the store interface and local implementation in `waffle/artifacts/`.

## Concepts

* **Artifact**: a bundle of named files plus a manifest (kind, small JSON metadata, and per
  file: size, sha256, media type, and dtype/shape for `.npy` arrays). Its reference
  `<kind>@sha256:<hex>` (`ArtifactRef`) is the hash of the canonical manifest, so identical
  content has an identical reference. Record revisions (`kind:name@sha256:...`) are told apart
  by the colon before the `@`.
* **Attempt**: one try at an operation. `attempt.json` is written at start, `outcome.json`
  once at the end (`succeeded`, `failed`, `cancelled`). No outcome means `started`: still
  running, or interrupted. Retries are new attempts; nothing is overwritten.
* **Provenance record**: "artifact A was produced by operation O (name, version, parameter
  digest, environment) in attempt T from inputs I", where each input has a role (`input`,
  `fitting_data`, `validation_data`, `reference`, ...) and is an artifact or a record revision.

Provenance sits beside the manifest, not inside it. Identity is content, so the same bytes
produced twice (or by two racing attempts) is one artifact; each production adds its own
immutable record. An artifact therefore may have several records, and lineage follows all of
them. If inputs must change identity, put them in the manifest metadata.

## Usage

```python
store = LocalArtifactStore(Path("store"))
op = OperationInfo(name="fit", version="1", parameters_digest=content_hash(params),
                   environment=capture_environment(["numpy"]))
with store.attempt(op, [InputLink(role="fitting_data", target=dataset_ref)]) as session:
    writer = session.new_artifact("model", {"order": 3})
    writer.write_array("coefficients.npy", coefficients)
    writer.write_json("report.json", report)
    ref = writer.commit()          # atomic; idempotent
store.inspect(ref)                 # manifest only, no payload opened
store.load_array(ref, "coefficients.npy", mmap=True)
store.verify(ref)                  # diagnostics; empty when intact
upstream(store, ref); dependents(store, dataset_ref, transitive=True)   # waffle.artifacts.lineage
```

The `with` block ends the attempt: `failed` on an `Exception`, `cancelled` on any other
`BaseException`, and re-raises either. `ArtifactStore` is the interface (references, names,
bytes, arrays; no paths); `LocalArtifactStore` is the directory implementation.

## On-disk layout

```
<root>/
  artifacts/<kind>/<hex>/        immutable once it exists
    manifest.json                canonical JSON; its hash is <hex>
    files/<name>                 payload
  attempts/<attempt_id>/         id = <UTC time>-<12 hex>; sorts by start
    attempt.json                 written once: operation, inputs, input fingerprint
    outcome.json                 written once at the end: state, diagnostics, produced refs
    staging/<kind>-<id>/         unpublished output (partial if it is still here)
  provenance/<hex>/<attempt_id>.json   written once per producing attempt
```

Immutable: everything under `artifacts/`. Append-only: `attempts/` (new directories, each file
written once) and `provenance/`. The only deletion is a staging directory that lost a publish
race to identical content. To inspect an attempt by hand:

```sh
cat store/attempts/<id>/attempt.json store/attempts/<id>/outcome.json
ls -R store/attempts/<id>/staging        # leftover output of a failed or interrupted attempt
python -m json.tool store/artifacts/<kind>/<hex>/manifest.json
grep -l '<kind>@sha256:<hex>' store/provenance/*/*.json   # who used this artifact
```

## Guarantees and assumptions

* **Atomic publication.** Files are written and fsynced in the attempt's staging directory,
  the manifest is written last, and the directory is renamed onto `artifacts/<kind>/<hex>`.
  Readers see a complete artifact or none. This assumes the store is on one local POSIX
  filesystem (macOS, Linux) where `rename` of a directory onto a non-existent path is atomic;
  network filesystems are not supported. A publisher that finds the destination present
  (`EEXIST`/`ENOTEMPTY`) knows identical content is already there, discards its copy, and
  returns the same reference.
* **Failure.** An exception or crash leaves its staging directory and an attempt record
  (`failed`/`cancelled`, or `started` if the process died). Staged output is never an
  artifact and never resolvable. The provenance file is written after the rename; a crash
  between the two leaves an artifact that `verify` flags with `artifact.no_provenance`
  (warning), and re-running `commit` on the same writer completes it.
* **Concurrency.** Safe for several processes and threads: attempt ids are unique, `outcome.json`
  and provenance files are created by write-then-hard-link so a reader never sees a partial
  file and a second writer cannot replace one.
* **Integrity.** `verify` recomputes sizes and digests and reports `artifact.file_corrupt`,
  `artifact.file_missing`, `artifact.file_unexpected`, `artifact.manifest_corrupt` (including a
  manifest that no longer hashes to its reference), and `artifact.not_found`. `inspect`
  checks the manifest hash. `read_bytes` and `load_array` check the file digest on every read.
  `load_array(..., mmap=True)` stays lazy and checks only size, dtype, and shape; call `verify`
  to check digests of mapped arrays.
* **Metadata without arrays.** `inspect` and `list_artifacts` read only manifests; array
  dtype and shape come from the manifest.
* **Scale.** `dependents` and `list_attempts` scan the provenance and attempt directories.
  There is no index; add a rebuildable one if stores grow large.
* Durability needs `fsync` to be honored by the filesystem. Artifacts are never deleted by the
  store; garbage collection is out of scope.
