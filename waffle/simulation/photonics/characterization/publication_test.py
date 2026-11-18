from __future__ import annotations

from pathlib import Path

import numpy as np

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.definition import Domain, InterfaceDefinition, Terminal
from waffle.contracts.component.parameters import (
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.mappings.mapping import (
    ChannelBinding,
    PortAnchor,
    ResponseChannel,
    ViewMapping,
)
from waffle.contracts.models.model import EvaluatorPayload, ModelRevision
from waffle.models.evaluate import evaluate_optical_scattering
from waffle.simulation.photonics.characterization.publication import (
    EVALUATOR_KEY,
    publish_waveguide,
    sample_evaluator,
)
from waffle.simulation.photonics.characterization.validation import validate_waveguide
from waffle.simulation.photonics.characterization.waveguide import extract_profile_ratio


def test_publication_and_exact_public_evaluation(tmp_path: Path) -> None:
    """Synthetic algebra fixture tests dispatch; it is not numerical qualification."""
    interface = InterfaceDefinition(
        id="interface:guide",
        terminals=(
            Terminal(name="a", domain=Domain.OPTICAL),
            Terminal(name="b", domain=Domain.OPTICAL),
        ),
    )
    template = ModelRevision(
        id="model:guide",
        interface=revision_of(interface),
        operating_conditions=OperatingConditionSchema(
            parameters=(ParameterSpec(name="frequency", dimension=Dimension.FREQUENCY),)
        ),
        payload=EvaluatorPayload(name="synthetic", version="1"),
    )
    profile = np.array([[1, 2, 1]], dtype=complex)

    def evidence(grid: float):
        return extract_profile_ratio(
            (2e14,),
            profile,
            profile * 1j,
            upstream_plane_m=1e-6,
            downstream_plane_m=2e-6,
            field_component="Hz",
            output_channel="b",
            excitation_channel="a",
            grid_spacing_m=grid,
            time_step_s=1e-17,
            steps=100,
        ).unwrap()

    fine, coarse = evidence(1e-8), evidence(2e-8)
    report = validate_waveguide(
        fine,
        coarse,
        {2e14: 1j},
        report_id="validation_report:fixture",
        reference_description="Synthetic scalar multiplication fixture",
        channels=("a", "b"),
    )
    store = LocalArtifactStore(tmp_path)
    raw = publish_waveguide(store, template, fine, report)
    assert raw.model is None
    published = publish_waveguide(
        store,
        template,
        fine,
        report,
        modal_qualification="Synthetic exact single forward profile fixture; not physical evidence",
    )
    model = published.model
    assert model is not None
    digest = "sha256:" + "a" * 64
    mapping = ViewMapping(
        id="view_mapping:guide",
        interface=revision_of(interface),
        layout=f"layout:guide@{digest}",
        problem=f"physical_problem:guide@{digest}",
        model=revision_of(model),
        bindings=tuple(
            ChannelBinding(
                terminal=channel,
                layout_port=channel,
                anchor=PortAnchor(
                    center=(index, 0), orientation=180 if index == 0 else 0
                ),
                simulation_port=channel,
                model_channel=channel,
            )
            for index, channel in enumerate(("a", "b"))
        ),
        responses=(ResponseChannel(output="b", excitation="a", status="computed"),),
    )
    evaluator = sample_evaluator(
        store, ModelRevision.model_validate_json(model.model_dump_json())
    )
    result = evaluate_optical_scattering(
        model,
        interface,
        OperatingValues(values={"frequency": Quantity(value=2e14, unit="Hz")}),
        mapping,
        (("b", "a"),),
        {EVALUATOR_KEY: evaluator},
    )
    assert result.unwrap() == {("b", "a"): 1j}
    rejected = evaluate_optical_scattering(
        model,
        interface,
        OperatingValues(values={"frequency": Quantity(value=2.001e14, unit="Hz")}),
        mapping,
        (("b", "a"),),
        {EVALUATOR_KEY: evaluator},
    )
    assert not rejected.ok
    assert not store.verify(published.evidence)
