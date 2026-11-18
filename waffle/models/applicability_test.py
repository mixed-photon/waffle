from __future__ import annotations

from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.diagnostics import Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    Normalization,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import (
    Analysis,
    Applicability,
    AxisCoverage,
    ExtrapolationPolicy,
    NumericInterval,
    OpticalScattering,
)
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.models.applicability import check_applicability


def _fixture() -> tuple[InterfaceDefinition, ModelRevision, ViewMapping]:
    interface = InterfaceDefinition(
        id=EntityId("interface", "guide"),
        terminals=(
            Terminal(name="a", domain=Domain.OPTICAL),
            Terminal(name="b", domain=Domain.OPTICAL),
        ),
    )
    model = ModelRevision(
        id=EntityId("model", "guide"),
        interface=revision_of(interface),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        payload=EvaluatorPayload(name="guide", version="1"),
        capability=OpticalScattering(observed_channels=(("b", "a"),)),
        applicability_spec=Applicability(
            axes=(
                AxisCoverage(
                    parameter="frequency",
                    sampled=(
                        Quantity(value=190, unit="THz"),
                        Quantity(value=200, unit="THz"),
                    ),
                    validated=NumericInterval(
                        minimum=Quantity(value=192, unit="THz"),
                        maximum=Quantity(value=198, unit="THz"),
                    ),
                ),
            )
        ),
    )
    ref = lambda kind: RevisionRef(EntityId(kind, "guide"), "sha256:" + "a" * 64)
    mapping = ViewMapping(
        id=EntityId("view_mapping", "guide"),
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
    return interface, model, mapping


def _check(
    frequency: Quantity,
    *,
    requested: tuple[tuple[str, str], ...] = (("b", "a"),),
    **changes: object,
) -> list[str]:
    interface, model, mapping = _fixture()
    if changes.get("model") is not None:
        model = changes["model"]  # type: ignore[assignment]
    if changes.get("mapping") is not None:
        mapping = changes["mapping"]  # type: ignore[assignment]
    diagnostics = check_applicability(
        model,
        interface,
        Analysis.OPTICAL_SCATTERING,
        OperatingValues(values={"frequency": frequency}),
        mapping=mapping,
        requested_channels=requested,
    )
    return [d.code for d in diagnostics]


def test_sampled_point_is_not_automatically_validated() -> None:
    codes = _check(Quantity(value=190_000_000, unit="MHz"))
    assert "model.outside_validated_coverage" in codes
    assert "model.extrapolation" not in codes


def test_extrapolation_policy_and_unknowns() -> None:
    assert "model.extrapolation" in _check(Quantity(value=210, unit="THz"))
    interface, model, mapping = _fixture()
    allowed = model.model_copy(
        update={
            "applicability_spec": Applicability(
                extrapolation=ExtrapolationPolicy.ALLOW_WITH_WARNING,
                axes=(
                    AxisCoverage(
                        parameter="frequency",
                        sampled=(
                            Quantity(value=190, unit="THz"),
                            Quantity(value=200, unit="THz"),
                        ),
                    ),
                ),
            )
        }
    )
    mapping = mapping.model_copy(update={"model": revision_of(allowed)})
    findings = check_applicability(
        allowed,
        interface,
        Analysis.OPTICAL_SCATTERING,
        OperatingValues(values={"frequency": Quantity(value=210, unit="THz")}),
        mapping=mapping,
    )
    assert all(item.severity is not Severity.ERROR for item in findings)
    assert {item.code for item in findings} >= {
        "model.extrapolation",
        "model.validated_coverage_unknown",
        "model.uncertainty_unknown",
    }


def test_partial_channels_remain_unavailable() -> None:
    assert "model.channel_unobserved" in _check(
        Quantity(value=195, unit="THz"), requested=(("a", "b"),)
    )
    _, _, mapping = _fixture()
    uncomputed = mapping.model_copy(
        update={
            "responses": (
                ResponseChannel(
                    output="b", excitation="a", status=ResponseStatus.UNCOMPUTED
                ),
            )
        }
    )
    assert "model.channel_uncomputed" in _check(
        Quantity(value=195, unit="THz"), mapping=uncomputed
    )


def test_mapping_terminal_must_belong_to_pinned_interface() -> None:
    _, _, mapping = _fixture()
    changed = mapping.model_copy(
        update={
            "bindings": (
                mapping.bindings[0].model_copy(update={"terminal": "missing"}),
                mapping.bindings[1],
            )
        }
    )
    assert "model.mapping_terminal_mismatch" in _check(
        Quantity(value=195, unit="THz"), mapping=changed
    )


def test_exact_interface_analysis_and_units() -> None:
    interface, model, mapping = _fixture()
    changed = interface.model_copy(update={"metadata": {"changed": "yes"}})
    found = check_applicability(
        model,
        changed,
        Analysis.OPTICAL_SCATTERING,
        OperatingValues(values={"frequency": Quantity(value=195, unit="THz")}),
        mapping=mapping,
    )
    assert "model.interface_mismatch" in {item.code for item in found}
    assert "model.analysis_unsupported" in {
        item.code
        for item in check_applicability(
            model, interface, Analysis.ELECTRICAL_DC, OperatingValues(), mapping=mapping
        )
    }
    assert "units.dimension_mismatch" in _check(Quantity(value=195, unit="um"))


def test_unknown_unit_and_mapping_conventions() -> None:
    assert "units.unknown_unit" in _check(Quantity(value=195, unit="mystery"))
    _, _, mapping = _fixture()
    changed_binding = mapping.bindings[0].model_copy(
        update={
            "normalization": Normalization.FIELD_AMPLITUDE,
            "reference_plane_offset_dbu": 10,
        }
    )
    changed_mapping = mapping.model_copy(
        update={"bindings": (changed_binding, mapping.bindings[1])}
    )
    codes = _check(Quantity(value=195, unit="THz"), mapping=changed_mapping)
    assert "model.normalization_unsupported" in codes
    assert "model.reference_plane_unsupported" in codes
    assert "model.frequency_nonpositive" in _check(Quantity(value=0, unit="THz"))


def test_duplicate_mapping_entries_are_rejected() -> None:
    _, _, mapping = _fixture()
    duplicate = mapping.model_copy(
        update={
            "bindings": (*mapping.bindings, mapping.bindings[0]),
            "responses": (*mapping.responses, mapping.responses[0]),
        }
    )
    codes = _check(Quantity(value=195, unit="THz"), mapping=duplicate)
    assert "model.mapping_duplicate_binding" in codes
    assert "model.mapping_duplicate_response" in codes


def test_pinned_scope_requires_context() -> None:
    interface, model, _ = _fixture()
    constrained = model.model_copy(
        update={
            "designs": (
                RevisionRef(EntityId("design", "pinned"), "sha256:" + "b" * 64),
            )
        }
    )
    codes = {
        item.code
        for item in check_applicability(
            constrained,
            interface,
            Analysis.OPTICAL_SCATTERING,
            OperatingValues(values={"frequency": Quantity(value=195, unit="THz")}),
        )
    }
    assert "model.design_required" in codes
