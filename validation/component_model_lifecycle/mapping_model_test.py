"""Exercise real physical preparation through mappings into a bounded evaluator.

The evaluator is a synthetic response fixture, not a validated device model.
"""

from __future__ import annotations

from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import (
    load_record,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import Applicability, OpticalScattering
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.design.mappings.validate import validate_mapping
from waffle.models.evaluate import evaluate_optical_scattering
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common.examples import request, straight_waveguide
from waffle.simulation.common.prepare import prepare_physical_problem


def test_prepared_ports_mapping_and_pinned_partial_model() -> None:
    layout = straight_waveguide()
    problem = prepare_physical_problem(layout, synthetic_soi(), request()).unwrap()
    interface = InterfaceDefinition(
        id=EntityId("interface", "synthetic_optical"),
        terminals=(
            Terminal(name="input", domain=Domain.OPTICAL),
            Terminal(name="output", domain=Domain.OPTICAL),
        ),
    )
    model = ModelRevision(
        id=EntityId("model", "synthetic_partial"),
        interface=revision_of(interface),
        payload=EvaluatorPayload(name="synthetic_constant", version="1"),
        capability=OpticalScattering(observed_channels=(("right", "left"),)),
        applicability_spec=Applicability(),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
    )
    mapping = ViewMapping(
        id=EntityId("view_mapping", "synthetic_partial"),
        interface=revision_of(interface),
        layout=revision_of(layout),
        problem=revision_of(problem),
        model=revision_of(model),
        bindings=tuple(
            ChannelBinding(
                terminal=terminal,
                layout_port=port.path,
                simulation_port=port.path,
                anchor=PortAnchor(center=port.center, orientation=port.orientation),
                model_channel=channel,
            )
            for terminal, channel, port in zip(
                ("input", "output"),
                ("left", "right"),
                problem.description.ports,
                strict=True,
            )
        ),
        responses=(
            ResponseChannel(
                output="right", excitation="left", status=ResponseStatus.COMPUTED
            ),
        ),
    )
    # Pin and reload every new contract before using it with prepared geometry.
    model = load_record(to_canonical_json(model), ModelRevision).unwrap()
    mapping = load_record(to_canonical_json(mapping), ViewMapping).unwrap()
    assert validate_mapping(mapping, interface, layout, problem, model) == []

    def synthetic_response(
        _model: ModelRevision,
        _conditions: OperatingValues,
        pairs: frozenset[tuple[str, str]],
    ) -> dict[tuple[str, str], complex]:
        return {pair: 0.75 + 0.25j for pair in pairs}

    conditions = OperatingValues(values={"frequency": Quantity(value=195, unit="THz")})
    registry = {("synthetic_constant", "1"): synthetic_response}
    result = evaluate_optical_scattering(
        model, interface, conditions, mapping, (("right", "left"),), registry
    )
    assert result.unwrap() == {("right", "left"): 0.75 + 0.25j}
    missing = evaluate_optical_scattering(
        model, interface, conditions, mapping, (("left", "right"),), registry
    )
    assert not missing.ok
    assert "model.channel_unobserved" in {d.code for d in missing.diagnostics}
    changed_problem = problem.model_copy(update={"metadata": {"revision": "changed"}})
    assert "mapping.revision_mismatch" in {
        d.code
        for d in validate_mapping(mapping, interface, layout, changed_problem, model)
    }
