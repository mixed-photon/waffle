"""A small coherent record chain (phase shifter) shared by tests and documentation."""

from __future__ import annotations

from dataclasses import dataclass

from waffle.contracts.component.definition import (
    ComponentDefinition,
    Domain,
    GeneratorSource,
    InterfaceDefinition,
    PhysicalImplementation,
    TechnologyRequirement,
    Terminal,
)
from waffle.contracts.component.design import ResolvedDesign, ToolPin
from waffle.contracts.component.parameters import (
    DesignParameterSchema,
    DesignValues,
    ParameterSpec,
)
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity

TECHNOLOGY = RevisionRef(EntityId("technology", "demo_si"), "sha256:" + "1" * 64)
GENERATOR = ToolPin(name="phase_shifter_gen", version="1.2.0")


def um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


@dataclass(frozen=True)
class PhaseShifterExample:
    interface: InterfaceDefinition
    component: ComponentDefinition
    implementation: PhysicalImplementation
    values: DesignValues
    design: ResolvedDesign

    @property
    def design_ref(self) -> RevisionRef:
        return revision_of(self.design)


def phase_shifter(length_um: float = 100.0) -> PhaseShifterExample:
    interface = InterfaceDefinition(
        id=EntityId("interface", "phase_shifter"),
        terminals=(
            Terminal(name="in", domain=Domain.OPTICAL, role="input"),
            Terminal(name="out", domain=Domain.OPTICAL, role="output"),
            Terminal(name="heater", domain=Domain.ELECTRICAL),
        ),
    )
    component = ComponentDefinition(
        id=EntityId("component", "phase_shifter"),
        interface=revision_of(interface),
        design_parameters=DesignParameterSchema(
            parameters=(
                ParameterSpec(
                    name="length", dimension=Dimension.LENGTH, minimum=um(1), maximum=um(1000)
                ),
                ParameterSpec(name="width", dimension=Dimension.LENGTH, default=um(0.5)),
            )
        ),
        metadata={"title": "Thermo-optic phase shifter"},
    )
    implementation = PhysicalImplementation(
        id=EntityId("implementation", "phase_shifter_gen"),
        component=revision_of(component),
        source=GeneratorSource(name=GENERATOR.name, version=GENERATOR.version),
        technology=TechnologyRequirement(technology=TECHNOLOGY.entity, layers=("WG", "HEATER")),
    )
    values = DesignValues(values={"length": um(length_um), "width": um(0.5)})
    design = ResolvedDesign(
        id=EntityId("design", f"phase_shifter_{length_um:g}um"),
        implementation=revision_of(implementation),
        parameters=values,
        technology=TECHNOLOGY,
        tools=(GENERATOR,),
    )
    return PhaseShifterExample(interface, component, implementation, values, design)
