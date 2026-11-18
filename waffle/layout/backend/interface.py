"""The layout backend interface: four independent capabilities and a descriptor.

A backend is any object with `info() -> BackendInfo`. What it can do is declared in its
`BackendInfo` and implemented by whichever of four small protocols it chooses:

* `LayoutGenerator`: build a `Layout` from a named generator, design values, and a technology;
* `LayoutImporter`: read GDS bytes (with an optional sidecar) into a `Layout`;
* `LayoutExporter`: write a `Layout` as GDS bytes (the sidecar is built by the caller with
  `waffle.layout.backend.packaging.export_package`);
* `GeometryOperations`: polygon merge and Boolean operations, which the IR does not have.

Callers ask for a capability with `generator_of`, `importer_of`, `exporter_of`, or
`geometry_of` and get a `Result`: a backend that lacks it yields a
`layout.backend.capability_unsupported` diagnostic, never an `AttributeError`. Signatures use
only Waffle contracts. Nothing here knows any upstream library; adapters import this module,
never the reverse. Shared conformance cases are in `waffle.layout.backend.conformance`.

Diagnostic codes backends share are in `BackendCode`, so a native backend reports exactly
what an external one does.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from waffle.contracts.component.definition import (
    GeneratorSource,
    PhysicalImplementation,
)
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.component.parameters import DesignParameterSchema, DesignValues
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import EntityIdField, revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.layout.layout import LayerSpec, Layout, Name
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.layout.formats.sidecar import GdsSidecar, Producer


def _layout_id(entity: EntityId) -> EntityId:
    if entity.kind != "layout":
        raise ValueError(f"{entity} is not a layout id")
    return entity


LayoutId = Annotated[EntityIdField, AfterValidator(_layout_id)]


class Capability(StrEnum):
    GENERATION = "generation"
    IMPORT = "import"
    EXPORT = "export"
    GEOMETRY = "geometry"


class GeometryOperation(StrEnum):
    MERGE = "merge"
    BOOLEAN = "boolean"


class BooleanOp(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"  # a minus b
    XOR = "xor"


class BackendCode(StrEnum):
    """Diagnostic codes shared by all layout backends."""

    CAPABILITY_UNSUPPORTED = "layout.backend.capability_unsupported"
    GENERATOR_UNKNOWN = "layout.backend.generator_unknown"
    REQUEST_INVALID = "layout.backend.request_invalid"
    LAYER_MISSING = "layout.backend.layer_missing"  # a needed layer is not in the table
    LAYER_UNMAPPED = "layout.backend.layer_unmapped"  # geometry on a pair the table lacks
    DEGENERATE_SHAPE = "layout.backend.degenerate_shape"  # zero area or too few vertices
    GRID_MISMATCH = "layout.backend.grid_mismatch"
    NAME_COLLISION = "layout.backend.name_collision"
    NAME_SANITIZED = "layout.backend.name_sanitized"  # info: a name was rewritten
    INSTANCE_NAMES_GENERATED = "layout.backend.instance_names_generated"  # info
    TOP_AMBIGUOUS = "layout.backend.top_ambiguous"
    PORT_UNSUPPORTED = "layout.backend.port_unsupported"
    NO_PORTS = "layout.backend.no_ports"  # info: imported without a sidecar
    IMPORT_FAILED = "layout.backend.import_failed"
    EXPORT_FAILED = "layout.backend.export_failed"


def diagnostic(
    code: BackendCode | str,
    message: str,
    *,
    severity: Severity = Severity.ERROR,
    subject: str | None = None,
    location: tuple[str | int, ...] = (),
) -> Diagnostic:
    return Diagnostic(
        severity=severity, code=str(code), message=message, subject=subject, location=location
    )


def with_diagnostics[T](result: Result[T], earlier: Iterable[Diagnostic]) -> Result[T]:
    """`result` with `earlier` findings placed before its own (an error among them voids it)."""
    combined = (*earlier, *result.diagnostics)
    return (
        Result(result.value, combined)
        if result.value is None
        else Result.of(result.value, combined)
    )


class GeneratorInfo(BaseModel):
    """A generator a backend offers: Waffle-owned name and exact version, its design
    parameters, and `example` values that must generate a valid layout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    parameters: DesignParameterSchema
    example: DesignValues


class BackendInfo(BaseModel):
    """What a backend is and can do. Capabilities follow from what is listed: a backend
    that lists generators can generate, one that lists import formats can import, and so on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer: Producer
    generators: tuple[GeneratorInfo, ...] = ()
    import_formats: tuple[str, ...] = ()
    export_formats: tuple[str, ...] = ()
    geometry_operations: tuple[GeometryOperation, ...] = ()

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            capability
            for capability, listed in (
                (Capability.GENERATION, self.generators),
                (Capability.IMPORT, self.import_formats),
                (Capability.EXPORT, self.export_formats),
                (Capability.GEOMETRY, self.geometry_operations),
            )
            if listed
        )

    def generator(self, name: str, version: str) -> GeneratorInfo | None:
        return next((g for g in self.generators if (g.name, g.version) == (name, version)), None)


class GenerationRequest(BaseModel):
    """Generate `generator` with `values` in `technology`. `layout_id` defaults to one the
    backend derives deterministically from the request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generator: GeneratorSource
    values: DesignValues
    technology: TechnologyRevision
    layout_id: LayoutId | None = None


class ImportRequest(BaseModel):
    """Import GDS bytes. With a `sidecar` the bytes are verified against it and it supplies
    ports, logical layer names, and the layout id. Without one `layers` maps GDS pairs to
    logical names, `layout_id` names the result, and there are no ports. `top` selects the
    top cell when the file has several."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sidecar: GdsSidecar | None = None
    layers: tuple[LayerSpec, ...] = ()
    layout_id: LayoutId | None = None
    top: Name | None = None

    @model_validator(mode="after")
    def _self_sufficient(self) -> Self:
        if self.sidecar is None and (not self.layers or self.layout_id is None):
            raise ValueError("an import needs a sidecar, or a layer table and a layout id")
        return self

    @property
    def table(self) -> tuple[LayerSpec, ...]:
        return self.sidecar.layers if self.sidecar else self.layers

    @property
    def result_id(self) -> EntityId:
        if self.sidecar is not None:
            return self.sidecar.layout.entity
        assert self.layout_id is not None
        return self.layout_id


class BooleanOperation(BaseModel):
    """`out := a <op> b`, for the shapes each cell holds itself, on layers of the layout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: BooleanOp
    a: Name
    b: Name
    out: Name


@runtime_checkable
class LayoutBackend(Protocol):
    def info(self) -> BackendInfo: ...


@runtime_checkable
class LayoutGenerator(LayoutBackend, Protocol):
    def generate(self, request: GenerationRequest) -> Result[Layout]:
        """The layout of the requested generator, valid under `validate_layout`, on the
        technology's grid and layer table. Problems are diagnostics, not exceptions."""
        ...


@runtime_checkable
class LayoutImporter(LayoutBackend, Protocol):
    def import_gds(self, data: bytes, request: ImportRequest) -> Result[Layout]:
        """The layout in `data`. Constructs the IR cannot express are errors, never dropped."""
        ...


@runtime_checkable
class LayoutExporter(LayoutBackend, Protocol):
    def export_gds(self, layout: Layout) -> Result[bytes]:
        """GDS bytes of a valid layout: hierarchy kept, one GDS pair per logical layer."""
        ...


@runtime_checkable
class GeometryOperations(LayoutBackend, Protocol):
    def merge(self, layout: Layout) -> Result[Layout]:
        """Union overlapping and abutting shapes per layer in every cell (hierarchy, ports,
        and instances are unchanged). A result with holes is an unsupported-shape error."""
        ...

    def boolean(self, layout: Layout, operation: BooleanOperation) -> Result[Layout]:
        """Apply `operation` in every cell, replacing the shapes on its `out` layer."""
        ...


def _lacks[T](backend: LayoutBackend, capability: Capability) -> Result[T]:
    info = backend.info()
    return Result(
        None,
        (
            diagnostic(
                BackendCode.CAPABILITY_UNSUPPORTED,
                f"backend {info.producer.name!r} does not support {capability.value}; "
                f"it supports {sorted(c.value for c in info.capabilities) or 'nothing'}",
                subject=info.producer.name,
            ),
        ),
    )


def generator_of(backend: LayoutBackend) -> Result[LayoutGenerator]:
    if Capability.GENERATION in backend.info().capabilities and isinstance(
        backend, LayoutGenerator
    ):
        return Result.of(backend)
    return _lacks(backend, Capability.GENERATION)


def importer_of(backend: LayoutBackend) -> Result[LayoutImporter]:
    if Capability.IMPORT in backend.info().capabilities and isinstance(backend, LayoutImporter):
        return Result.of(backend)
    return _lacks(backend, Capability.IMPORT)


def exporter_of(backend: LayoutBackend) -> Result[LayoutExporter]:
    if Capability.EXPORT in backend.info().capabilities and isinstance(backend, LayoutExporter):
        return Result.of(backend)
    return _lacks(backend, Capability.EXPORT)


def geometry_of(backend: LayoutBackend) -> Result[GeometryOperations]:
    if Capability.GEOMETRY in backend.info().capabilities and isinstance(
        backend, GeometryOperations
    ):
        return Result.of(backend)
    return _lacks(backend, Capability.GEOMETRY)


def layer_table(technology: TechnologyRevision) -> tuple[LayerSpec, ...]:
    """The technology's layer table as layout layers (logical name and GDS pair)."""
    return tuple(
        LayerSpec(name=layer.name, layer=layer.gds_layer, datatype=layer.gds_datatype)
        for layer in technology.layers
    )


def request_for_design(
    design: ResolvedDesign, implementation: PhysicalImplementation, technology: TechnologyRevision
) -> Result[GenerationRequest]:
    """The generation request of a resolved design, after checking that `implementation` and
    `technology` are exactly the revisions the design pins. The layout is named
    `layout:<design name>`."""
    found: list[Diagnostic] = []
    subject = str(design.id)
    if revision_of(implementation) != design.implementation:
        found.append(
            diagnostic(
                BackendCode.REQUEST_INVALID,
                f"implementation is not the pinned revision {design.implementation}",
                subject=subject,
            )
        )
    if revision_of(technology) != design.technology:
        found.append(
            diagnostic(
                BackendCode.REQUEST_INVALID,
                f"technology is not the pinned revision {design.technology}",
                subject=subject,
            )
        )
    source = implementation.source
    if not isinstance(source, GeneratorSource):
        found.append(
            diagnostic(
                BackendCode.REQUEST_INVALID,
                "implementation is not produced by a generator",
                subject=subject,
            )
        )
    if found or not isinstance(source, GeneratorSource):
        return Result(None, tuple(found))
    return Result.of(
        GenerationRequest(
            generator=source,
            values=design.parameters,
            technology=technology,
            layout_id=EntityId("layout", design.id.name),
        )
    )
