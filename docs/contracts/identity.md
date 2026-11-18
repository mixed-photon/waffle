# Component and model identity contracts

Records live in `waffle/contracts/component` and `waffle/contracts/models`. The foundation
pieces they share are in `waffle/contracts/foundation`: `units`, `records`, `result`.
Services that use them are in `waffle/design/components` and `waffle/design/instances`.

## Records

Every record is a frozen pydantic model with `kind`, `schema_version`, a stable `id`
(`EntityId`, whose kind equals the record kind), and `metadata` (free text only).

| Kind | Record | Holds |
|---|---|---|
| `interface` | `InterfaceDefinition` | ordered terminals (name, optical or electrical domain, optional role) |
| `component` | `ComponentDefinition` | interface revision, design parameter schema |
| `implementation` | `PhysicalImplementation` | component revision, source (generator name + exact version, or layout artifact), technology id and layer names |
| `design` | `ResolvedDesign` | implementation revision, concrete design values, technology revision, exact tool pins |
| `circuit` | `Circuit` | instances (name, design revision, placement) and terminal-to-terminal connections |
| `specimen` | `Specimen` | fabricated design revision, sample identity (lot, wafer, die, site), fabrication notes |
| `model` | `ModelRevision` | interface revision, optional implementation and design scope, operating-condition schema, fitted coefficients, payload, evidence, `capabilities`, `applicability` |

Records refer to each other only by `RevisionRef` (`kind:name@sha256:<hex>`), and each
reference field accepts only its own kind. Many instances may name one design revision,
and many specimens one fabricated design revision. `Specimen.design` is the "fabricated
revision"; whether a design was actually fabricated is not recorded here.
`Circuit` structure (duplicate names, unknown terminals, domain mismatch) is checked by
`validate_circuit`, because it needs the referenced interfaces.

## Parameter roles

| Role | Declared by | Values stored on | Type |
|---|---|---|---|
| design parameters | `ComponentDefinition.design_parameters` | `ResolvedDesign.parameters` | `DesignParameterSchema`, `DesignValues` |
| operating conditions | `ModelRevision.operating_conditions` | nowhere; supplied per analysis | `OperatingConditionSchema`, `OperatingValues` |
| fitted coefficients | none | `ModelRevision.coefficients` | `FittedCoefficients` |

Each type carries a `role` literal. A field of one role rejects another role's payload,
`ParameterSchema.check` reports `params.role_mismatch` for values of the wrong role, and
records forbid unknown fields, so a `ResolvedDesign` cannot carry operating conditions.

Quantities are `{"value": 0.5, "unit": "um"}` with units from a closed table
(`foundation/units.py`; `degC` is an absolute temperature, no log units). A `Quantity`
with an unknown unit still loads, so it can be reported. Checking returns diagnostics:
`units.unknown_unit`, `units.dimension_mismatch`, `params.missing`, `params.unknown`,
`params.out_of_bounds`, `params.role_mismatch`, each located at `("values", name)`.

## Revisions

A record's revision is the hash of the canonical JSON of the **whole** record, with
`id` as the entity (`revision_of`). Nothing is excluded: changing `metadata` changes the
revision. Published records are immutable (frozen models); a change is a new revision.
Operation-specific fingerprints that ignore descriptive fields belong to MOD-018. Keep
descriptive text in `metadata` so those can find it. `EntityId` and `RevisionRef`
serialize as strings. Note the models are frozen but their `dict` fields are not deeply
immutable; the revision digest is what detects change.

## Schema versions and migration

Each record class fixes `kind` and `schema_version` as `Literal` defaults. `load_record`
(from JSON text or a mapping) does the following:

1. a wrong kind or non-integer version gives `record.kind_mismatch`;
2. a version newer than the class gives `record.unsupported_version`;
3. an older version runs registered `vN -> vN+1` steps one by one, or gives
   `record.no_migration_path` at the first gap;
4. the result is validated, with each pydantic error becoming a `record.invalid`
   diagnostic located at the field.

To change a schema: bump the `Literal` on the record, and register
`@migration("<kind>", old_version)` on a function that maps the old payload dict to the
new one (the version field is advanced for you). Old records keep their old digest;
loading and re-publishing one yields a new revision.

## Extension points owned by MOD-009

`capabilities` and `applicability` are tuples of `Extension(name, version, body)`:
`name` selects a body schema, `version` its revision, and `body` is unvalidated JSON for
now. MOD-009 replaces the body with typed models chosen by `name` and `version`. That
bumps `ModelRevision.schema_version`, with a migration that moves existing bodies
across unchanged. Nothing else in the record changes.

## Example

```python
from waffle.contracts.component.design import ToolPin
from waffle.contracts.component.parameters import DesignValues
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.design.components.resolve import resolve_design

result = resolve_design(
    id=EntityId.parse("design:ps_100um"),
    definition=component,            # ComponentDefinition
    implementation=implementation,   # PhysicalImplementation (generator "gen" 1.2.0)
    values=DesignValues(values={"length": Quantity(value=100, unit="um")}),
    technology=technology_ref,       # RevisionRef of technology:demo_si
    tools=[ToolPin(name="gen", version="1.2.0")],
)
design = result.unwrap()             # or read result.diagnostics
text = to_canonical_json(design)
assert revision_of(load_record(text, type(design)).unwrap()) == revision_of(design)
```

`waffle/contracts/component/examples.py` builds a full working chain used by the tests.
