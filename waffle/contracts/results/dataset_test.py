from __future__ import annotations

from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import (
    load_record,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.results.dataset import (
    MeasurementDataset,
    PowerObservation,
    WavelengthAxis,
)


def test_dataset_round_trip_preserves_unknown_and_zero_uncertainty() -> None:
    ref = lambda kind: RevisionRef(EntityId(kind, "x"), "sha256:" + "a" * 64)
    artifact = "measurement_raw@sha256:" + "b" * 64
    dataset = MeasurementDataset(
        id=EntityId("measurement_dataset", "x"),
        specimen=ref("specimen"),
        design=ref("design"),
        mapping=ref("view_mapping"),
        raw_artifact=artifact,
        data_artifact=artifact,
        axis=WavelengthAxis(values=(1550.0,), unit="nm"),
        observations=(
            PowerObservation(output_channel="out", values=(1.0,), unit="mW"),
            PowerObservation(
                output_channel="in", values=(0.0,), unit="mW", standard_deviation=(0.0,)
            ),
        ),
        synthetic=True,
    )
    restored = load_record(to_canonical_json(dataset), MeasurementDataset).unwrap()
    assert restored == dataset
    assert revision_of(restored) == revision_of(dataset)
    assert restored.observations[0].standard_deviation is None
    assert restored.observations[1].standard_deviation == (0.0,)
