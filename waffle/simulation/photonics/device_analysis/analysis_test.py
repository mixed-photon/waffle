from __future__ import annotations

from dataclasses import replace

import pytest

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
    ObservableKind,
)
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.physics.approximations import Polarization
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.common.backend import (
    Acquisition,
    BackendRun,
    ProgressEvent,
    RunState,
)
from waffle.simulation.common.examples import request as physical_request
from waffle.simulation.common.examples import straight_waveguide
from waffle.simulation.common.fake_backend import FakeBackend
from waffle.simulation.common.prepare import prepare_physical_problem
from waffle.simulation.photonics.device_analysis.analysis import analyze


def fixture() -> tuple[PhysicalProblem, AnalysisRequest]:
    problem = prepare_physical_problem(
        straight_waveguide(), synthetic_soi(), physical_request("2d")
    ).unwrap()
    request = AnalysisRequest(
        id=EntityId("analysis_request", "field"),
        problem=revision_of(problem),
        polarization=Polarization.TE,
        excitations=(
            Excitation(kind="point", center_dbu=(1000, 0), wavelength_pm=1_550_000),
        ),
        observables=(
            Observable(
                name="field", kind=ObservableKind.RAW_FIELD, field_component="Hz"
            ),
        ),
        numerical=NumericalSettings(
            grid_spacing_pm=100_000, time_step_as=100, steps=100
        ),
        backend_settings=BackendSettings(
            backend="fake", version="1.0", schema_version=1
        ),
    )
    return problem, request


def test_success_records_settings_progress_and_acquired_kind() -> None:
    problem, request = fixture()
    fake = FakeBackend(
        BackendRun(
            RunState.COMPLETE,
            (
                Acquisition(
                    "field", ObservableKind.RAW_FIELD, (1.0, 2.0), "A/m", ("x",), (2,)
                ),
            ),
            100,
        )
    )
    progress: list[ProgressEvent] = []
    outcome = analyze(problem, request, fake, progress=progress.append)
    assert outcome.complete and outcome.missing == ()
    assert outcome.acquired[0].payload == (1.0, 2.0)
    assert (
        outcome.backend_version == "1.0"
        and outcome.request.backend_settings == fake.observed_settings
    )
    assert progress == [ProgressEvent(100, 100, "execute")]


def test_preflight_rejects_stale_problem_polarization_and_backend_settings_without_execution() -> (
    None
):
    problem, request = fixture()
    stale = request.model_copy(
        update={
            "problem": revision_of(
                problem.model_copy(update={"metadata": {"changed": "yes"}})
            ),
            "polarization": Polarization.TM,
            "backend_settings": BackendSettings(
                backend="fake",
                version="1.0",
                schema_version=1,
                parameters={"unknown": True},
            ),
        }
    )
    fake = FakeBackend(BackendRun(RunState.COMPLETE, (), 100))
    outcome = analyze(problem, stale, fake)
    assert outcome.state is RunState.REJECTED and fake.calls == 0
    assert {d.code for d in outcome.diagnostics} >= {
        "analysis.problem.stale",
        "analysis.polarization.mismatch",
        "analysis.backend.settings",
    }


def test_cancelled_and_partial_outputs_remain_inspectable() -> None:
    problem, request = fixture()
    second = Observable(name="flux", kind=ObservableKind.RAW_FLUX, port="o2")
    request = request.model_copy(update={"observables": (*request.observables, second)})
    fake = FakeBackend(
        BackendRun(
            RunState.CANCELLED,
            (
                Acquisition(
                    "field", ObservableKind.RAW_FIELD, (3.0,), "A/m", ("x",), (1,)
                ),
            ),
            40,
        )
    )
    outcome = analyze(problem, request, fake)
    assert outcome.state is RunState.CANCELLED and not outcome.complete
    assert [a.name for a in outcome.acquired] == ["field"] and outcome.missing == (
        "flux",
    )
    assert outcome.completed_steps == 40
    fake.next_run = BackendRun(
        RunState.FAILED,
        (Acquisition("field", ObservableKind.RAW_FIELD, (4.0,), "A/m", ("x",), (1,)),),
        50,
        (
            Diagnostic(
                severity=Severity.ERROR,
                code="backend.runtime.failed",
                message="synthetic failure",
            ),
        ),
    )
    failed = analyze(problem, request, fake)
    assert failed.state is RunState.FAILED and failed.acquired[0].payload == (4.0,)
    assert failed.diagnostics[0].code == "backend.runtime.failed"


def test_raw_acquisition_cannot_satisfy_normalized_request() -> None:
    problem, request = fixture()
    mapping = ViewMapping(
        id=EntityId("view_mapping", "wg"),
        interface=RevisionRef(EntityId("interface", "i"), "sha256:" + "a" * 64),
        layout=problem.layout,
        problem=revision_of(problem),
        bindings=(
            ChannelBinding(
                terminal="in",
                layout_port="o1",
                anchor=PortAnchor(center=(0, 0), orientation=180),
                simulation_port="o1",
                model_channel="c1",
            ),
            ChannelBinding(
                terminal="out",
                layout_port="o2",
                anchor=PortAnchor(center=(10000, 0), orientation=0),
                simulation_port="o2",
                model_channel="c2",
            ),
        ),
        responses=(
            ResponseChannel(
                output="c2", excitation="c1", status=ResponseStatus.COMPUTED
            ),
        ),
    )
    request = request.model_copy(
        update={
            "mapping": revision_of(mapping),
            "excitations": (
                Excitation(kind="port_mode", port="o1", wavelength_pm=1_550_000),
            ),
            "observables": (
                Observable(
                    name="s21",
                    kind=ObservableKind.MODAL_SCATTERING,
                    port="o2",
                    source_index=0,
                    output_channel="c2",
                    excitation_channel="c1",
                ),
            ),
        }
    )
    fake = FakeBackend(
        BackendRun(
            RunState.COMPLETE,
            (
                Acquisition(
                    "s21", ObservableKind.RAW_FIELD, (1.0,), "V/m", ("x",), (1,)
                ),
            ),
            100,
        )
    )
    unsupported = analyze(problem, request, fake, mapping)
    assert unsupported.state is RunState.REJECTED and fake.calls == 0
    assert "analysis.observable.unsupported" in {
        d.code for d in unsupported.diagnostics
    }
    fake.capabilities_value = replace(
        fake.capabilities_value,
        observables=frozenset(
            {*fake.capabilities_value.observables, ObservableKind.MODAL_SCATTERING}
        ),
    )
    wrong = analyze(problem, request, fake, mapping)
    assert (
        wrong.state is RunState.PARTIAL
        and wrong.acquired == ()
        and wrong.missing == ("s21",)
    )
    assert "analysis.output.kind" in {d.code for d in wrong.diagnostics}

    # An uncomputed mapping response is a valid acquisition target. The channel
    # pair must still correspond to the selected physical source/output ports.
    mapping = mapping.model_copy(
        update={
            "responses": (
                ResponseChannel(
                    output="c2", excitation="c1", status=ResponseStatus.UNCOMPUTED
                ),
            )
        }
    )
    request = request.model_copy(update={"mapping": revision_of(mapping)})
    assert analyze(problem, request, fake, mapping).state is RunState.PARTIAL
    swapped = request.model_copy(
        update={
            "observables": (
                request.observables[0].model_copy(
                    update={"output_channel": "c1", "excitation_channel": "c2"}
                ),
            )
        }
    )
    rejected = analyze(problem, swapped, fake, mapping)
    assert rejected.state is RunState.REJECTED
    assert "analysis.mapping.channel" in {d.code for d in rejected.diagnostics}
    shifted_binding = mapping.bindings[1].model_copy(
        update={"reference_plane_offset_dbu": 5}
    )
    shifted_mapping = mapping.model_copy(
        update={"bindings": (mapping.bindings[0], shifted_binding)}
    )
    shifted_request = request.model_copy(
        update={"mapping": revision_of(shifted_mapping)}
    )
    shifted = analyze(problem, shifted_request, fake, shifted_mapping)
    assert shifted.state is RunState.REJECTED
    assert "analysis.mapping.convention" in {d.code for d in shifted.diagnostics}
    stale = analyze(problem, request, fake, shifted_mapping)
    assert "analysis.mapping.stale" in {d.code for d in stale.diagnostics}


def test_reduction_wavelength_mode_and_point_location_are_checked_before_execution() -> (
    None
):
    problem, request = fixture()
    bad_source = Excitation(
        kind="point", center_dbu=(100_000, 0), wavelength_pm=1_310_000, mode_order=1
    )
    request = request.model_copy(update={"excitations": (bad_source,)})
    fake = FakeBackend(BackendRun(RunState.COMPLETE, (), 100))
    outcome = analyze(problem, request, fake)
    assert outcome.state is RunState.REJECTED and fake.calls == 0
    assert {d.code for d in outcome.diagnostics} >= {
        "analysis.excitation.outside_domain",
        "analysis.reduction.wavelength",
        "analysis.mode.unsupported",
    }


def test_backend_preflight_and_complete_claim_are_checked() -> None:
    problem, request = fixture()

    class RejectingFake(FakeBackend):
        def validate_problem(
            self, problem: PhysicalProblem, request: AnalysisRequest
        ) -> tuple[Diagnostic, ...]:
            del problem, request
            return (
                Diagnostic(
                    severity=Severity.ERROR,
                    code="backend.material.unsupported",
                    message="not supported",
                ),
            )

    fake = RejectingFake(BackendRun(RunState.COMPLETE, (), 100))
    assert analyze(problem, request, fake).state is RunState.REJECTED
    assert fake.calls == 0
    normal = FakeBackend(
        BackendRun(
            RunState.COMPLETE,
            (
                Acquisition(
                    "field", ObservableKind.RAW_FIELD, (1.0,), "A/m", ("x",), (1,)
                ),
            ),
            80,
        )
    )
    incomplete = analyze(problem, request, normal)
    assert incomplete.state is RunState.PARTIAL and "analysis.output.steps" in {
        d.code for d in incomplete.diagnostics
    }
    normal.next_run = BackendRun(
        RunState.COMPLETE,
        (Acquisition("field", ObservableKind.RAW_FIELD, (1.0,), "A/m", ("x",), (1,)),),
        100,
        (
            Diagnostic(
                severity=Severity.ERROR, code="backend.runtime.failed", message="error"
            ),
        ),
    )
    erroneous = analyze(problem, request, normal)
    assert erroneous.state is RunState.PARTIAL and not erroneous.complete
    normal.next_run = BackendRun(
        RunState.COMPLETE,
        (Acquisition("field", ObservableKind.RAW_FIELD, (1.0,), "V/m", ("x",), (1,)),),
        100,
    )
    wrong_units = analyze(problem, request, normal)
    assert wrong_units.state is RunState.PARTIAL and wrong_units.missing == ("field",)
    assert "analysis.output.units" in {d.code for d in wrong_units.diagnostics}


def test_pre_cancelled_request_does_not_launch_and_empty_payload_is_invalid() -> None:
    problem, request = fixture()

    class Cancelled:
        def cancelled(self) -> bool:
            return True

    fake = FakeBackend(BackendRun(RunState.COMPLETE, (), 100))
    outcome = analyze(problem, request, fake, cancellation=Cancelled())
    assert outcome.state is RunState.CANCELLED and fake.calls == 0
    with pytest.raises(ValueError, match="payload"):
        Acquisition("field", ObservableKind.RAW_FIELD, None, "V/m", ("x",), (1,))
    with pytest.raises(ValueError, match="finite"):
        Acquisition(
            "field", ObservableKind.RAW_FIELD, (float("nan"),), "A/m", ("x",), (1,)
        )
    with pytest.raises(ValueError, match="shape"):
        Acquisition(
            "field",
            ObservableKind.RAW_FIELD,
            ((1.0, 2.0), (3.0, 4.0)),
            "A/m",
            ("y", "x"),
            (2, 3),
        )


def test_success_retains_backend_preflight_warnings() -> None:
    problem, request = fixture()
    warning = Diagnostic(
        severity=Severity.WARNING,
        code="backend.mesh.coarse",
        message="synthetic mesh warning",
    )

    class WarningBackend(FakeBackend):
        def validate_problem(
            self, problem: PhysicalProblem, request: AnalysisRequest
        ) -> tuple[Diagnostic, ...]:
            return (warning,)

    backend = WarningBackend(
        BackendRun(
            RunState.COMPLETE,
            (
                Acquisition(
                    "field", ObservableKind.RAW_FIELD, (1.0,), "A/m", ("x",), (1,)
                ),
            ),
            request.numerical.steps,
        )
    )
    outcome = analyze(problem, request, backend)
    assert outcome.complete
    assert warning in outcome.diagnostics
