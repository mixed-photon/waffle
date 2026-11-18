# Model capability and applicability

`ModelRevision` schema v2 adds `capability` and `applicability_spec`. Its interface,
implementation, and design references are exact revision references. The original
`capabilities` and `applicability` extension tuples remain loadable as legacy data;
their JSON bodies are never interpreted as executable claims. Loading a v1 record
migrates it to v2 with the typed fields unset. The resulting v2 record has a new
canonical revision hash; existing v1 references remain historical references.

`OpticalScattering` describes a complex, power-wave normalized frequency-domain
response. `observed_channels` lists only response elements actually available as
`(output, excitation)` pairs. An omitted pair is unavailable, not zero. A
`ViewMapping` pins the model and interface revisions and records whether each pair
was computed. The applicability preflight checks both the capability's observed
pairs and the mapping's computed pairs and channel bindings. Requested channels
must use identity modal transforms and zero reference-plane offsets; phase
correction and alternative normalization are not implemented.

`AxisCoverage.sampled` lists known sample coordinates. `validated` is a separate
inclusive interval, for example one supported by held-out interpolation tests.
Neither claim implies the other. `None` means unknown; an empty sample tuple means
known to have no samples. Quantities are compared in SI units. Out-of-sample
requests fail by default; `allow_with_warning` permits them with an explicit
extrapolation diagnostic. Requests outside validated coverage and unknown
coverage produce warnings. Unknown uncertainty is distinct from a known zero
dimensionless amplitude bound.

Call `waffle.models.applicability.check_applicability` before evaluation. It returns
structured diagnostics, including warnings for unsupported evidence claims. The
caller must reject any result with error severity and expose warnings to the user.
`waffle.models.evaluate.evaluate_optical_scattering` performs that preflight, dispatches
an evaluator registered under the payload's exact `(name, version)` pin, and accepts
only finite complex values for every requested pair. Missing or extra pairs are
errors. Applications provide evaluator implementations; no analytic device formula
is built into this milestone. `ElectricalDescriptor` shares the same revision
metadata and applicability representation but has no electrical evaluator here.
