from __future__ import annotations

import cmath
import math
from collections.abc import Callable
from pathlib import Path

import pytest
from waffle_pdk_demo import ReferenceComponent, mmi, waveguide

from third_party.gdsfactory.backend import GdsfactoryBackend
from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.foundation.records import revision_of
from waffle.experiments.bindings.store import publish_binding, replay_binding
from waffle.layout.backend.interface import ImportRequest
from waffle.layout.formats.sidecar import read_package
from waffle.layout.services.compare import compare_layouts


@pytest.mark.parametrize("builder", [waveguide, mmi])
def test_reference_physical_consistency_and_gds(
    builder: Callable[[], ReferenceComponent], tmp_path: Path
) -> None:
    reference = builder()
    assert reference.problem.design == revision_of(reference.design)
    matrix = reference.evaluate()
    names = [t.name for t in reference.interface.terminals]
    for a in names:
        for b in names:
            assert matrix[a, b] == matrix[b, a]
            inner = sum(matrix[o, a].conjugate() * matrix[o, b] for o in names)
            assert inner == pytest.approx(1 if a == b else 0, abs=1e-12)
    path = tmp_path / "component.gds"
    reference.export(path)
    package = read_package(path).unwrap()
    imported = (
        GdsfactoryBackend()
        .import_gds(package.gds, ImportRequest(sidecar=package.sidecar))
        .unwrap()
    )
    assert compare_layouts(imported, reference.layout) == []
    circuit, binding = reference.bind()
    assert binding.circuit == revision_of(circuit)
    store = LocalArtifactStore(tmp_path / "store")
    artifact = publish_binding(store, binding, reference.snapshots()).unwrap()
    assert replay_binding(store, artifact).unwrap().binding == binding


def test_waveguide_phase_and_band() -> None:
    reference = waveguide()
    expected = cmath.exp(2j * math.pi * reference.effective_index * 10 / 1.55)
    assert reference.evaluate()["o2", "o1"] == pytest.approx(expected)
    assert reference.evaluate()["o1", "o1"] == 0j
    with pytest.raises(ValueError):
        reference.evaluate(1.7)
