"""Real CPU acceptance; failed qualification stays raw evidence, never a fake S21."""

from __future__ import annotations

import json
import os
from pathlib import Path

from waffle_pdk_demo import waveguide

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.models.model import ModelRevision
from waffle.contracts.physics.approximations import Polarization
from waffle.models.evaluate import evaluate_optical_scattering
from waffle.simulation.photonics.characterization.publication import (
    EVALUATOR_KEY,
    sample_evaluator,
)
from waffle.simulation.photonics.characterization.recipe import characterize_waveguide


def test_measured_characterization() -> None:
    root = Path(
        os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR", "/tmp/waffle-characterization")
    )
    root.mkdir(parents=True, exist_ok=True)
    component = waveguide(length_um=2, polarization=Polarization.TM)
    worker = Path("third_party/beamz/worker").resolve()
    result = characterize_waveguide(
        component.problem,
        component.model,
        component.mapping,
        command=(str(worker),),
        root=root / "workers",
        store=LocalArtifactStore(root / "store"),
    )
    summary = {
        "passed": result.report.passed,
        "backward_fraction": result.backward_fraction,
        "standing_wave_residual": result.standing_wave_residual,
        "fitted_effective_index": result.fitted_effective_index,
        "model": str(result.publication.model_artifact),
        "evidence": str(result.publication.evidence),
        "validation": str(result.publication.validation),
        "attempts": [e.attempt_id for e in result.executions],
        "report": result.report.model_dump(mode="json"),
    }
    (root / "measured.json").write_text(json.dumps(summary, indent=2))
    print("MEASURED", json.dumps(summary))
    assert len(result.executions) == 4
    assert all(e.artifact is not None for e in result.executions)
    assert result.report.passed, summary
    assert result.publication.model is not None
    assert result.mapping is not None
    restored = ModelRevision.model_validate_json(
        result.publication.model.model_dump_json()
    )
    frequency = 299792458.0 / (1550000 * 1e-12)
    evaluated = evaluate_optical_scattering(
        restored,
        component.interface,
        OperatingValues(values={"frequency": Quantity(value=frequency, unit="Hz")}),
        result.mapping,
        (("o2", "o1"),),
        {EVALUATOR_KEY: sample_evaluator(LocalArtifactStore(root / "store"), restored)},
        design=component.design,
    )
    assert evaluated.ok, evaluated.diagnostics
    assert set(evaluated.unwrap()) == {("o2", "o1")}
    summary["observed_forward_real"] = evaluated.unwrap()[("o2", "o1")].real
    summary["observed_forward_imag"] = evaluated.unwrap()[("o2", "o1")].imag
    (root / "measured.json").write_text(json.dumps(summary, indent=2))
