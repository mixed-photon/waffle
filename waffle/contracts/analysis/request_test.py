from __future__ import annotations

from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
    ObservableKind,
)
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import (
    load_record,
    revision_of,
    to_canonical_json,
)


def test_request_is_versioned_and_pins_exact_problem() -> None:
    request = AnalysisRequest(
        id=EntityId("analysis_request", "raw"),
        problem=RevisionRef(EntityId("physical_problem", "p"), "sha256:" + "a" * 64),
        excitations=(
            Excitation(kind="point", center_dbu=(0, 0), wavelength_pm=1_550_000),
        ),
        observables=(
            Observable(
                name="field", kind=ObservableKind.RAW_FIELD, field_component="Ez"
            ),
        ),
        numerical=NumericalSettings(
            grid_spacing_pm=100_000, time_step_as=100, steps=100
        ),
        backend_settings=BackendSettings(
            backend="fake", version="1.0", schema_version=1
        ),
    )
    restored = load_record(to_canonical_json(request), AnalysisRequest).unwrap()
    assert restored == request
    assert revision_of(restored) == revision_of(request)
