# Bounded measurement ingestion

`waffle.experiments.measurements.ingest.import_measurement` accepts UTF-8 JSON bytes,
a `Specimen`, a `ViewMapping`, its pinned `PhysicalProblem`, a dataset `EntityId`,
and an `ArtifactStore`. The
file's `specimen`, `design`, and `mapping` references must exactly match the
supplied specimen revision, its fabricated design revision, and the mapping
revision. The physical problem must be the mapping's exact pinned revision and
must name the specimen's fabricated design revision. Every observed channel must
appear as a `measurement_channel` in the
mapping. The importer returns `Result[MeasurementDataset]`; invalid input produces
structured `measurement.*` diagnostics and publishes no artifact.

The version 1 format is deliberately small:

```json
{
  "schema_version": 1,
  "specimen": "specimen:die_1@sha256:<64 hex digits>",
  "design": "design:waveguide@sha256:<64 hex digits>",
  "mapping": "view_mapping:waveguide_measurement@sha256:<64 hex digits>",
  "axis": {"name": "wavelength", "values": [1500.0, 1550.0], "unit": "nm"},
  "observations": [
    {
      "kind": "optical_power",
      "output_channel": "output",
      "excitation_channel": "input",
      "values": [1.0, 1.2],
      "unit": "mW",
      "standard_deviation": null
    }
  ],
  "conditions": {"temperature": {"value": 25.0, "unit": "degC"}},
  "calibration": {"state": "raw", "method": null, "reference": null},
  "synthetic": true
}
```

`synthetic` is required; set it to `true` for generated fixtures and `false` for
instrument data. The example above is a synthetic fixture, not a measured
device. This importer supports one strictly increasing, positive wavelength axis
with at most 4096 samples, linear power units (`W`, `mW`, `uW`), and one or more
nonnegative optical power traces. It does not infer complex amplitudes or phase.
Only observed channel pairs appear in the dataset; unobserved pairs remain absent.
An observation whose input and output channel are the same represents reflection
and is accepted.
An omitted or null `standard_deviation` means unknown uncertainty. A vector of
zeros is an explicit zero and is retained as such. A calibrated payload must
declare its method; calibration is recorded, not performed during import.

The original JSON bytes are stored unchanged as `source.json` in a
`measurement_raw` artifact. Its provenance links the specimen, fabricated design,
and mapping revisions. `MeasurementDataset.raw_artifact` and `data_artifact`
initially point to that artifact. `scale_power(dataset, factor, store=...)` is a
bounded example of processing: it checks the source artifact, writes a new
`measurement_processed` artifact, and returns a new dataset revision with its
`parent` set to the previous revision. Its provenance links the current data
artifact, raw artifact, and parent record. Processing never overwrites raw bytes;
unknown uncertainty remains unknown and known standard deviations scale with the
power values. Before processing, the stored data is integrity checked and parsed
against the supplied dataset metadata. The returned dataset is an immutable,
content-addressed record; callers that need durable record lookup must publish
its canonical JSON in their record catalog. The artifact store publishes its raw
and processed data, not the record catalog.

Import and processing record observations and lineage only. They do not validate
model agreement, mark a model as qualified, or claim a full scattering matrix.
