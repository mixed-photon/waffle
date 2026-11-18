from __future__ import annotations

from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
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
from waffle.models.evaluate import evaluate_optical_scattering


def _fixture() -> tuple[
    ModelRevision, InterfaceDefinition, ViewMapping, OperatingValues
]:
    interface = InterfaceDefinition(
        id=EntityId("interface", "guide_eval"),
        terminals=(
            Terminal(name="a", domain=Domain.OPTICAL),
            Terminal(name="b", domain=Domain.OPTICAL),
        ),
    )
    model = ModelRevision(
        id=EntityId("model", "guide_eval"),
        interface=revision_of(interface),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        payload=EvaluatorPayload(name="stub", version="1"),
        capability=OpticalScattering(observed_channels=(("b", "a"),)),
        applicability_spec=Applicability(),
    )
    ref = lambda kind: RevisionRef(EntityId(kind, "guide_eval"), "sha256:" + "a" * 64)
    mapping = ViewMapping(
        id=EntityId("view_mapping", "guide_eval"),
        interface=revision_of(interface),
        layout=ref("layout"),
        problem=ref("physical_problem"),
        model=revision_of(model),
        bindings=(
            ChannelBinding(
                terminal="a",
                layout_port="a",
                anchor=PortAnchor(center=(0, 0), orientation=180),
                simulation_port="a",
                model_channel="a",
            ),
            ChannelBinding(
                terminal="b",
                layout_port="b",
                anchor=PortAnchor(center=(10, 0), orientation=0),
                simulation_port="b",
                model_channel="b",
            ),
        ),
        responses=(
            ResponseChannel(output="b", excitation="a", status=ResponseStatus.COMPUTED),
        ),
    )
    operating = OperatingValues(values={"frequency": Quantity(value=195, unit="THz")})
    return model, interface, mapping, operating


def test_pinned_evaluator_returns_only_requested_response() -> None:
    model, interface, mapping, operating = _fixture()
    result = evaluate_optical_scattering(
        model,
        interface,
        operating,
        mapping,
        (("b", "a"),),
        {("stub", "1"): lambda _m, _o, _pairs: {("b", "a"): 0.5 + 0.25j}},
    )
    assert result.ok
    assert result.unwrap() == {("b", "a"): 0.5 + 0.25j}
    assert ("a", "b") not in result.unwrap()


def test_missing_evaluator_or_response_is_diagnostic() -> None:
    model, interface, mapping, operating = _fixture()
    missing = evaluate_optical_scattering(
        model,
        interface,
        operating,
        mapping,
        (("b", "a"),),
        {("stub", "2"): lambda _m, _o, _pairs: {}},
    )
    assert "model.evaluator_unavailable" in {d.code for d in missing.diagnostics}
    incomplete = evaluate_optical_scattering(
        model,
        interface,
        operating,
        mapping,
        (("b", "a"),),
        {("stub", "1"): lambda _m, _o, _pairs: {}},
    )
    assert "model.response_missing" in {d.code for d in incomplete.diagnostics}
    assert not incomplete.ok


def test_invalid_and_extra_response_rejected() -> None:
    model, interface, mapping, operating = _fixture()
    bad = evaluate_optical_scattering(
        model,
        interface,
        operating,
        mapping,
        (("b", "a"),),
        {
            ("stub", "1"): lambda _m, _o, _pairs: {
                ("b", "a"): complex(float("nan"), 0),
                ("a", "b"): 0j,
            }
        },
    )
    assert {d.code for d in bad.diagnostics} >= {
        "model.response_invalid",
        "model.response_unrequested",
    }
    assert not bad.ok


def test_evaluator_receives_effective_default_conditions() -> None:
    model, interface, mapping, _ = _fixture()
    model = model.model_copy(
        update={
            "operating_conditions": OperatingConditionSchema(
                parameters=(
                    ParameterSpec(
                        name="frequency",
                        dimension=Dimension.FREQUENCY,
                        default=Quantity(value=195, unit="THz"),
                    ),
                )
            )
        }
    )
    mapping = mapping.model_copy(update={"model": revision_of(model)})
    seen: list[Quantity] = []

    def evaluator(
        _model: ModelRevision,
        values: OperatingValues,
        _pairs: frozenset[tuple[str, str]],
    ) -> dict[tuple[str, str], complex]:
        seen.append(values.values["frequency"])
        return {("b", "a"): 1 + 0j}

    result = evaluate_optical_scattering(
        model,
        interface,
        OperatingValues(),
        mapping,
        (("b", "a"),),
        {("stub", "1"): evaluator},
    )
    assert result.ok
    assert seen == [Quantity(value=195, unit="THz")]


def test_invalid_response_key_is_diagnostic() -> None:
    model, interface, mapping, operating = _fixture()
    result = evaluate_optical_scattering(
        model,
        interface,
        operating,
        mapping,
        (("b", "a"),),
        {("stub", "1"): lambda _m, _o, _pairs: {"bad": 1 + 0j}},
    )  # type: ignore[dict-item]
    assert "model.response_invalid_key" in {d.code for d in result.diagnostics}
    assert not result.ok
