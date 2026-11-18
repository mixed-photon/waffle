"""Original, unrestricted demonstration components; never fabrication-qualified."""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
from pathlib import Path

from waffle.contracts.component.definition import (
    ComponentDefinition,
    Domain,
    GeneratorSource,
    InterfaceDefinition,
    PhysicalImplementation,
    TechnologyRequirement,
    Terminal,
)
from waffle.contracts.component.design import (
    Circuit,
    CircuitInstance,
    ResolvedDesign,
    ToolPin,
)
from waffle.contracts.component.parameters import (
    DesignValues,
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.experiments.binding import ExperimentBinding
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import Record, revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.layout.layout import Layout
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Applicability,
    NumericInterval,
    OpticalScattering,
)
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    WavelengthRange,
)
from waffle.contracts.technology.pdk import Access, Pdk, PdkManifest
from waffle.contracts.technology.revision import (
    LayoutLayer,
    Patterned,
    ProcessLevel,
    ProcessStack,
    TechnologyRevision,
)
from waffle.design.components.resolve import resolve_design
from waffle.experiments.bindings.resolve import resolve_binding
from waffle.layout.backend.interface import request_for_design
from waffle.layout.backend.packaging import export_package
from waffle.layout.formats.sidecar import GdsPackage, write_package
from waffle.models.evaluate import evaluate_optical_scattering
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.common.request import (
    EffectiveIndexSpec,
    Margins,
    PhysicalRequest,
)
from waffle.simulation.photonics.device_analysis.slab import (
    SlabLayer,
    SlabStack,
    effective_index,
)

C = 299792458.0
VERSION = "0.1.0"


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def demo_technology() -> TechnologyRevision:
    """Made-up symmetric 220 nm silicon strip in silica, flat indices 3.476/1.444."""
    band = WavelengthRange(minimum=um(1.5), maximum=um(1.6))
    return TechnologyRevision(
        id=EntityId("technology", "demo_strip"),
        dbu_pm=1000,
        manufacturing_grid_pm=5000,
        optical_band=band,
        layers=(LayoutLayer(name="WG", gds_layer=1, gds_datatype=0),),
        materials=tuple(
            Material(
                name=name,
                optical=ConstantIndex(n=n),
                validity=band,
                source="Original synthetic demo constant; no foundry data",
            )
            for name, n in (("core", 3.476), ("cladding", 1.444))
        ),
        stack=ProcessStack(
            background="cladding",
            levels=(
                ProcessLevel(
                    name="core",
                    z_min=um(0),
                    thickness=um(0.22),
                    material="core",
                    pattern=Patterned(layers=("WG",), fill="cladding"),
                ),
            ),
        ),
        metadata={"notice": "Synthetic unrestricted demo. Not for fabrication."},
    )


def demo_pdk() -> Pdk:
    technology = demo_technology()
    return Pdk(
        manifest=PdkManifest(
            id=EntityId("pdk", "demo_strip"),
            version=VERSION,
            waffle="==0.1.0",
            access=Access.PUBLIC,
            license="MIT",
            technologies=(revision_of(technology),),
        ),
        technologies=(technology,),
    )


@dataclass(frozen=True)
class ReferenceComponent:
    interface: InterfaceDefinition
    component: ComponentDefinition
    implementation: PhysicalImplementation
    design: ResolvedDesign
    technology: TechnologyRevision
    layout: Layout
    problem: PhysicalProblem
    model: ModelRevision
    mapping: ViewMapping
    effective_index: float
    wavelength_um: float
    polarization: Polarization

    @property
    def channels(self) -> tuple[tuple[str, str], ...]:
        return self.model.capability.observed_channels

    def operating(self, wavelength_um: float | None = None) -> OperatingValues:
        wavelength = self.wavelength_um if wavelength_um is None else wavelength_um
        return OperatingValues(
            values={"frequency": Quantity(value=C / (wavelength * 1e-6), unit="Hz")}
        )

    def evaluate(
        self, wavelength_um: float | None = None
    ) -> dict[tuple[str, str], complex]:
        return evaluate_optical_scattering(
            self.model,
            self.interface,
            self.operating(wavelength_um),
            self.mapping,
            self.channels,
            {("waffle.demo.analytic", VERSION): analytic_evaluator},
            design=self.design,
        ).unwrap()

    def gds_package(self) -> Result[GdsPackage]:
        from third_party.gdsfactory.backend import GdsfactoryBackend

        return export_package(
            GdsfactoryBackend(),
            self.layout,
            design=revision_of(self.design),
            technology=revision_of(self.technology),
            implementation=revision_of(self.implementation),
        )

    def export(self, path: str | Path) -> Path:
        return write_package(Path(path), self.gds_package().unwrap())

    def bind(self) -> tuple[Circuit, ExperimentBinding]:
        """Resolve an exact analytic model for one circuit instance."""
        circuit = Circuit(
            id=EntityId("circuit", self.design.id.name),
            instances=(
                CircuitInstance(name="device", design=revision_of(self.design)),
            ),
        )
        result = resolve_binding(
            EntityId("experiment_binding", self.design.id.name),
            circuit,
            {revision_of(self.design): self.design},
            {revision_of(self.implementation): self.implementation},
            {revision_of(self.component): self.component},
            {revision_of(self.interface): self.interface},
            {"device": (self.model,)},
            {revision_of(self.model): self.mapping},
            {"device": self.operating()},
            {"device": self.channels},
        )
        if result.binding is None:
            raise ValueError(result.diagnostics)
        return circuit, result.binding

    def snapshots(self) -> tuple[Record, ...]:
        circuit, _ = self.bind()
        return (
            circuit,
            self.interface,
            self.component,
            self.implementation,
            self.design,
            self.technology,
            self.layout,
            self.problem,
            self.model,
            self.mapping,
        )


def analytic_evaluator(
    model: ModelRevision, operating: OperatingValues, pairs: frozenset[tuple[str, str]]
) -> dict[tuple[str, str], complex]:
    data = model.metadata
    wavelength_m = C / operating.values["frequency"].si
    vertical = effective_index(
        SlabStack(1.444, (SlabLayer(0.22e-6, 3.476),), 1.444),
        wavelength_m,
        Polarization(data["polarization"]),
    )
    assert vertical is not None
    # Vertical TE maps to scalar Hz with lateral TM continuity; vertical TM to Ez/TE.
    neff = effective_index(
        SlabStack(1.444, (SlabLayer(0.5e-6, vertical),), 1.444),
        wavelength_m,
        Polarization.TM
        if Polarization(data["polarization"]) is Polarization.TE
        else Polarization.TE,
    )
    assert neff is not None
    phase = cmath.exp(
        2j * math.pi * neff * float(data["length_um"]) * 1e-6 / wavelength_m
    )
    if data["device"] == "straight":
        return {pair: 0j if pair[0] == pair[1] else phase for pair in pairs}
    # Ideal reciprocal lossless four-port quadrature hybrid in declared port order.
    matrix = ((0, 0, 1, 1j), (0, 0, 1j, 1), (1, 1j, 0, 0), (1j, 1, 0, 0))
    return {
        pair: phase * matrix[int(pair[0][1:]) - 1][int(pair[1][1:]) - 1] / math.sqrt(2)
        for pair in pairs
    }


def _build(
    device: str, length_um: float, wavelength_um: float, polarization: Polarization
) -> ReferenceComponent:
    from third_party.gdsfactory.backend import GdsfactoryBackend

    backend = GdsfactoryBackend()
    generator = next(
        g for g in backend.info().generators if g.name == f"waffle.gdsfactory.{device}"
    )
    technology = demo_technology()
    names = tuple(f"o{i}" for i in range(1, 3 if device == "straight" else 5))
    interface = InterfaceDefinition(
        id=EntityId("interface", device),
        terminals=tuple(Terminal(name=n, domain=Domain.OPTICAL) for n in names),
    )
    component = ComponentDefinition(
        id=EntityId("component", device),
        interface=revision_of(interface),
        design_parameters=generator.parameters,
    )
    implementation = PhysicalImplementation(
        id=EntityId("implementation", device),
        component=revision_of(component),
        source=GeneratorSource(name=generator.name, version=generator.version),
        technology=TechnologyRequirement(technology=technology.id, layers=("WG",)),
    )
    values = DesignValues(
        values={"length": um(length_um)}
        if device == "straight"
        else {"gap_mmi": um(0.3)}
    )
    design = resolve_design(
        id=EntityId("design", device),
        definition=component,
        implementation=implementation,
        values=values,
        technology=revision_of(technology),
        tools=(ToolPin(name=generator.name, version=generator.version),),
        technology_record=technology,
    ).unwrap()
    layout = backend.generate(
        request_for_design(design, implementation, technology).unwrap()
    ).unwrap()
    problem = prepare_physical_problem(
        layout,
        technology,
        PhysicalRequest(
            id=EntityId("physical_problem", device),
            dimensionality="2d",
            design=revision_of(design),
            margins=Margins(lateral=um(2), vertical=um(1)),
            reduction=EffectiveIndexSpec(
                wavelength=um(wavelength_um), polarization=polarization
            ),
        ),
    ).unwrap()
    channels = tuple((a, b) for a in names for b in names)
    model = ModelRevision(
        id=EntityId("model", device),
        interface=revision_of(interface),
        implementations=(revision_of(implementation),),
        designs=(revision_of(design),),
        payload=EvaluatorPayload(name="waffle.demo.analytic", version=VERSION),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        capability=OpticalScattering(observed_channels=channels),
        applicability_spec=Applicability(
            operating_ranges={
                "frequency": NumericInterval(
                    minimum=Quantity(value=C / 1.6e-6, unit="Hz"),
                    maximum=Quantity(value=C / 1.5e-6, unit="Hz"),
                )
            }
        ),
        metadata={
            "device": device,
            "length_um": str(length_um),
            "polarization": polarization.value,
            "assumptions": "Lossless reciprocal idealization; exp(-i omega t); zero reflections; no empirical validation",
        },
    )
    ports = next(c for c in layout.cells if c.name == layout.top).ports
    mapping = ViewMapping(
        id=EntityId("view_mapping", device),
        interface=revision_of(interface),
        layout=revision_of(layout),
        problem=revision_of(problem),
        model=revision_of(model),
        bindings=tuple(
            ChannelBinding(
                terminal=p.name,
                layout_port=p.name,
                simulation_port=p.name,
                model_channel=p.name,
                measurement_channel=p.name,
                anchor=PortAnchor(center=p.center, orientation=p.orientation),
            )
            for p in ports
        ),
        responses=tuple(
            ResponseChannel(output=a, excitation=b, status=ResponseStatus.COMPUTED)
            for a, b in channels
        ),
    )
    vertical = effective_index(
        SlabStack(1.444, (SlabLayer(0.22e-6, 3.476),), 1.444),
        wavelength_um * 1e-6,
        polarization,
    )
    neff = effective_index(
        SlabStack(1.444, (SlabLayer(0.5e-6, vertical),), 1.444),
        wavelength_um * 1e-6,
        Polarization.TM if polarization is Polarization.TE else Polarization.TE,
    )
    assert neff is not None
    return ReferenceComponent(
        interface,
        component,
        implementation,
        design,
        technology,
        layout,
        problem,
        model,
        mapping,
        neff,
        wavelength_um,
        polarization,
    )


def waveguide(
    length_um: float = 10,
    wavelength_um: float = 1.55,
    polarization: Polarization = Polarization.TE,
) -> ReferenceComponent:
    return _build("straight", length_um, wavelength_um, polarization)


def mmi(
    wavelength_um: float = 1.55, polarization: Polarization = Polarization.TE
) -> ReferenceComponent:
    """Four-port 2x2 MMI geometry with an explicitly idealized quadrature response."""
    return _build("mmi2x2", 25.5, wavelength_um, polarization)
