from __future__ import annotations

from waffle.contracts.component.definition import TechnologyRequirement
from waffle.contracts.component.design import ToolPin
from waffle.contracts.component.examples import GENERATOR, TECHNOLOGY, phase_shifter, um
from waffle.contracts.component.parameters import DesignValues
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.design.components.resolve import resolve_design
from waffle.pdk.testing.synthetic import TECHNOLOGY_ID, synthetic_soi

ID = EntityId("design", "ps_100um")


def resolve(values: DesignValues | None = None, **overrides: object):  # type: ignore[no-untyped-def]
    ex = phase_shifter()
    args: dict[str, object] = {
        "id": ID,
        "definition": ex.component,
        "implementation": ex.implementation,
        "values": values or DesignValues(values={"length": um(100)}),
        "technology": TECHNOLOGY,
        "tools": [GENERATOR],
    }
    args.update(overrides)
    return resolve_design(**args)  # type: ignore[arg-type]


def codes(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.code for d in result.diagnostics]


def test_resolution_fills_defaults_and_pins_dependencies() -> None:
    ex = phase_shifter()
    design = resolve().unwrap()
    assert design.parameters.values == {"length": um(100), "width": um(0.5)}
    assert design.implementation == revision_of(ex.implementation)
    assert design.technology == TECHNOLOGY
    assert design.tools == (GENERATOR,)
    # Resolving the same inputs yields the same revision as the hand-built example.
    assert revision_of(design.model_copy(update={"id": ex.design.id})) == ex.design_ref


def test_missing_and_invalid_parameters_are_structured_diagnostics() -> None:
    result = resolve(DesignValues(values={"width": Quantity(value=1.0, unit="ps"), "x": um(1)}))
    assert not result.ok
    assert sorted(codes(result)) == ["params.missing", "params.unknown", "units.dimension_mismatch"]
    by_code = {d.code: d for d in result.diagnostics}
    assert by_code["params.missing"].location == ("parameters", "values", "length")
    assert by_code["params.missing"].subject == "design:ps_100um"


def test_out_of_bounds_parameter() -> None:
    assert codes(resolve(DesignValues(values={"length": um(5000)}))) == ["params.out_of_bounds"]


def test_implementation_must_target_the_definition_revision() -> None:
    other = phase_shifter().component.model_copy(update={"metadata": {"title": "changed"}})
    assert codes(resolve(definition=other)) == ["design.component_mismatch"]


def test_technology_must_match_the_requirement() -> None:
    wrong = RevisionRef(EntityId("technology", "other"), "sha256:" + "2" * 64)
    assert codes(resolve(technology=wrong)) == ["design.technology_mismatch"]


def test_generator_version_must_be_pinned_exactly() -> None:
    assert codes(resolve(tools=[])) == ["design.missing_tool_pin"]
    assert codes(resolve(tools=[ToolPin(name=GENERATOR.name, version="9.9")])) == [
        "design.missing_tool_pin"
    ]
    assert codes(resolve(tools=[GENERATOR, GENERATOR])) == ["design.duplicate_tool_pin"]


def test_tool_pin_order_does_not_change_the_revision() -> None:
    backend = ToolPin(name="backend", version="1")
    a = resolve(tools=[GENERATOR, backend]).unwrap()
    b = resolve(tools=[backend, GENERATOR]).unwrap()
    assert revision_of(a) == revision_of(b)


def resolve_in_technology(layers: tuple[str, ...], record=None):  # type: ignore[no-untyped-def]
    technology = synthetic_soi()
    implementation = phase_shifter().implementation.model_copy(
        update={"technology": TechnologyRequirement(technology=TECHNOLOGY_ID, layers=layers)}
    )
    return resolve(
        implementation=implementation,
        technology=revision_of(technology),
        technology_record=technology if record is None else record,
    )


def test_technology_record_satisfying_the_requirement_resolves() -> None:
    assert resolve_in_technology(("WG", "SLAB")).ok


def test_technology_record_must_contain_the_required_layers() -> None:
    result = resolve_in_technology(("WG", "HEATER"))
    assert not result.ok
    assert codes(result) == ["technology.requirement_unknown_layer"]
    assert result.diagnostics[0].location == ("technology", "layers", 1)


def test_technology_record_must_be_the_pinned_revision() -> None:
    other = synthetic_soi().model_copy(update={"metadata": {"notice": "edited"}})
    assert codes(resolve_in_technology(("WG",), record=other)) == [
        "design.technology_record_mismatch"
    ]
