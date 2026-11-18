from __future__ import annotations

from pathlib import Path

import pytest

from waffle.artifacts.local import LocalArtifactStore
from waffle.characterization.calibration.power import (
    EVALUATOR_KEY,
    LIGHT_SPEED_M_S,
    CalibratedResponse,
    Capture,
    PowerFitSpec,
    calibrate_power_gain,
    calibrated_evaluator,
)
from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.design import SampleIdentity, Specimen
from waffle.contracts.component.examples import phase_shifter
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ResponseStatus,
    ViewMapping,
)
from waffle.contracts.models.capability import OpticalScattering
from waffle.contracts.models.model import (
    EvaluatorPayload,
    EvidenceKind,
    EvidenceRef,
    ModelRevision,
)
from waffle.contracts.physics.problem import (
    Boundaries3D,
    BoundaryCondition,
    Domain3D,
    Extrusion3D,
    Face,
    PhysicalProblem,
    Port3D,
)
from waffle.contracts.results.dataset import (
    Calibration,
    MeasurementPayload,
    PowerObservation,
    WavelengthAxis,
)
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    WavelengthRange,
)
from waffle.experiments.measurements.ingest import import_measurement
from waffle.models.evaluate import ChannelPair, evaluate_optical_scattering


def test_gain_uses_fitting_capture_only_and_publishes_verifiable_comparison(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    design = phase_shifter().design
    design_ref = revision_of(design)
    interface = InterfaceDefinition(
        id=EntityId("interface", "calibration"),
        terminals=tuple(
            Terminal(name=name, domain=Domain.OPTICAL) for name in ("input", "output")
        ),
    )
    pair = ("output", "input")
    model = ModelRevision(
        id=EntityId("model", "simulated_fixture"),
        interface=revision_of(interface),
        designs=(design_ref,),
        evidence=(EvidenceRef(kind=EvidenceKind.SIMULATION, ref=design_ref),),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        capability=OpticalScattering(observed_channels=(pair,)),
        payload=EvaluatorPayload(name="synthetic_simulation", version="1"),
    )
    layout = RevisionRef(EntityId("layout", "calibration"), "sha256:" + "a" * 64)
    ports = tuple(
        Port3D(
            path=port,
            layer="WG",
            domain=Domain.OPTICAL,
            center=(x, 0),
            width=2,
            orientation=orientation,
            z_min_pm=0,
            z_max_pm=200_000,
        )
        for port, x, orientation in (("p1", 0, 180), ("p2", 10, 0))
    )
    problem = PhysicalProblem(
        id=EntityId("physical_problem", "calibration"),
        design=design_ref,
        layout=layout,
        top_cell="top",
        technology=design.technology,
        dbu_pm=1000,
        description=Extrusion3D(
            domain=Domain3D(
                lo=(-5, -5),
                hi=(15, 5),
                z_min_pm=0,
                z_max_pm=200_000,
                boundaries=Boundaries3D(
                    **{face.value: BoundaryCondition() for face in Face}
                ),
            ),
            materials=(
                Material(
                    name="glass",
                    optical=ConstantIndex(n=1.44),
                    validity=WavelengthRange(
                        minimum=Quantity(value=1, unit="um"),
                        maximum=Quantity(value=2, unit="um"),
                    ),
                    source="fixture",
                ),
            ),
            background="glass",
            films=(),
            prisms=(),
            ports=ports,
        ),
    )
    mapping = ViewMapping(
        id=EntityId("view_mapping", "calibration"),
        interface=revision_of(interface),
        layout=layout,
        problem=revision_of(problem),
        model=revision_of(model),
        bindings=tuple(
            ChannelBinding(
                terminal=terminal,
                layout_port=port.path,
                anchor=PortAnchor(center=port.center, orientation=port.orientation),
                simulation_port=port.path,
                model_channel=terminal,
                measurement_channel=terminal,
            )
            for terminal, port in zip(("input", "output"), ports, strict=True)
        ),
        responses=(
            ResponseChannel(
                output="output", excitation="input", status=ResponseStatus.COMPUTED
            ),
        ),
    )

    def capture(name: str, measured_mw: float, *, has_incident: bool = True) -> Capture:
        specimen = Specimen(
            id=EntityId("specimen", name),
            design=design_ref,
            sample=SampleIdentity(lot="synthetic"),
        )
        payload = MeasurementPayload(
            specimen=revision_of(specimen),
            design=design_ref,
            mapping=revision_of(mapping),
            synthetic=True,
            axis=WavelengthAxis(values=(1550.0,), unit="nm"),
            observations=(
                PowerObservation(
                    output_channel="output",
                    excitation_channel="input",
                    values=(measured_mw,),
                    unit="mW",
                ),
            ),
            conditions={"incident_power": Quantity(value=1, unit="mW")}
            if has_incident
            else {},
            calibration=Calibration(
                state="calibrated", method="synthetic", reference="fixture"
            ),
        )
        dataset = import_measurement(
            payload.model_dump_json().encode(),
            id=EntityId("measurement_dataset", name),
            specimen=specimen,
            mapping=mapping,
            problem=problem,
            store=store,
        ).unwrap()
        return Capture(dataset, specimen)

    def baseline(
        _model: ModelRevision,
        _operating: OperatingValues,
        _pairs: frozenset[ChannelPair],
    ) -> dict[ChannelPair, complex]:
        return {pair: 0.48 + 0.64j}

    fitting = capture("fit", 0.32)
    validation = capture("check", 0.33)

    def calibrate(held_out: Capture) -> Result[CalibratedResponse]:
        return calibrate_power_gain(
            store,
            base=model,
            interface=interface,
            mapping=mapping,
            design=design,
            problem=problem,
            fitting=fitting,
            validation=held_out,
            pair=pair,
            spec=PowerFitSpec(validation_max_error=0.02),
            evaluators={("synthetic_simulation", "1"): baseline},
        )

    output = calibrate(validation).unwrap()
    assert output.report.power_gain == pytest.approx(0.5)
    assert output.report.validation_error == pytest.approx(0.01)
    assert output.model.id == model.id and revision_of(output.model) != revision_of(
        model
    )
    assert output.model.applicability_spec.axes[0].validated is None
    assert {link.role for link in store.provenance_of(output.artifact)[0].inputs} >= {
        "fitting_data",
        "validation_data",
        "simulation_model",
    }

    evaluator = calibrated_evaluator(store, output.model).unwrap()
    exact = OperatingValues(
        values={
            "frequency": Quantity(
                value=LIGHT_SPEED_M_S / (1_550_000 * 1e-12), unit="Hz"
            )
        }
    )
    response = evaluate_optical_scattering(
        output.model,
        interface,
        exact,
        output.mapping,
        (pair,),
        {EVALUATOR_KEY: evaluator},
        design=design,
    )
    assert response.unwrap()[pair] == pytest.approx((0.48 + 0.64j) * 0.5**0.5)
    assert not evaluate_optical_scattering(
        output.model,
        interface,
        exact,
        output.mapping,
        (("input", "output"),),
        {EVALUATOR_KEY: evaluator},
        design=design,
    ).ok

    assert "calibration.data_overlap" in {
        d.code for d in calibrate(fitting).diagnostics
    }
    assert "calibration.incident_power" in {
        d.code
        for d in calibrate(
            capture("unknown_source", 0.33, has_incident=False)
        ).diagnostics
    }
    failed = calibrate(capture("bad_check", 0.75))
    assert failed.value is None
    assert "calibration.validation_failed" in {d.code for d in failed.diagnostics}
    assert len(store.list_artifacts("power_calibration")) == 2
