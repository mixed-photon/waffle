from __future__ import annotations

import json
from pathlib import Path

from waffle.artifacts.lineage import upstream
from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.definition import Domain
from waffle.contracts.component.design import SampleIdentity, Specimen
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.mappings.mapping import ChannelBinding, PortAnchor, ViewMapping
from waffle.contracts.physics.problem import (
    Boundaries3D,
    BoundaryCondition,
    Domain3D,
    Extrusion3D,
    PhysicalProblem,
    Port3D,
)
from waffle.contracts.results.dataset import (
    MeasurementDataset,
    MeasurementPayload,
    PowerObservation,
    WavelengthAxis,
)
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    WavelengthRange,
)
from waffle.experiments.measurements.ingest import import_measurement, scale_power


def fixture() -> tuple[Specimen, ViewMapping, PhysicalProblem, MeasurementPayload]:
    def ref(kind: str) -> RevisionRef:
        return RevisionRef(EntityId(kind, "x"), "sha256:" + "a" * 64)

    specimen = Specimen(
        id=EntityId("specimen", "die_1"),
        design=ref("design"),
        sample=SampleIdentity(lot="synthetic_fixture", die="1"),
    )
    material = Material(
        name="si",
        optical=ConstantIndex(n=3.5),
        validity=WavelengthRange(
            minimum=Quantity(value=1.2, unit="um"),
            maximum=Quantity(value=1.7, unit="um"),
        ),
        source="synthetic_fixture",
    )
    problem = PhysicalProblem(
        id=EntityId("physical_problem", "measurement"),
        layout=ref("layout"),
        top_cell="top",
        technology=ref("technology"),
        design=specimen.design,
        dbu_pm=1000,
        description=Extrusion3D(
            domain=Domain3D(
                lo=(0, 0),
                hi=(10, 10),
                z_min_pm=0,
                z_max_pm=100,
                boundaries=Boundaries3D(
                    **{
                        name: BoundaryCondition()
                        for name in (
                            "x_min",
                            "x_max",
                            "y_min",
                            "y_max",
                            "z_min",
                            "z_max",
                        )
                    }
                ),
            ),
            materials=(material,),
            background="si",
            films=(),
            prisms=(),
            ports=tuple(
                Port3D(
                    path=name,
                    layer="WG",
                    domain=Domain.OPTICAL,
                    center=center,
                    width=2,
                    orientation=orientation,
                    z_min_pm=0,
                    z_max_pm=100,
                )
                for name, center, orientation in (
                    ("p0", (0, 0), 180),
                    ("p1", (10, 0), 0),
                )
            ),
        ),
    )
    mapping = ViewMapping(
        id=EntityId("view_mapping", "measurement"),
        interface=ref("interface"),
        layout=ref("layout"),
        problem=revision_of(problem),
        bindings=(
            ChannelBinding(
                terminal="a",
                layout_port="p0",
                anchor=PortAnchor(center=(0, 0), orientation=180),
                simulation_port="p0",
                model_channel="m0",
                measurement_channel="input",
            ),
            ChannelBinding(
                terminal="b",
                layout_port="p1",
                anchor=PortAnchor(center=(10, 0), orientation=0),
                simulation_port="p1",
                model_channel="m1",
                measurement_channel="output",
            ),
        ),
    )
    payload = MeasurementPayload(
        specimen=revision_of(specimen),
        design=specimen.design,
        mapping=revision_of(mapping),
        axis=WavelengthAxis(values=(1500.0, 1550.0), unit="nm"),
        observations=(
            PowerObservation(
                output_channel="output",
                excitation_channel="input",
                values=(1.0, 1.2),
                unit="mW",
            ),
        ),
        synthetic=True,
    )
    return specimen, mapping, problem, payload


def codes(result: Result[MeasurementDataset]) -> set[str]:
    return {diagnostic.code for diagnostic in result.diagnostics}


def test_measurement_mapping_requires_existing_optical_physical_port(
    tmp_path: Path,
) -> None:
    specimen, mapping, problem, payload = fixture()
    changed = mapping.model_copy(
        update={
            "bindings": (
                mapping.bindings[0].model_copy(update={"simulation_port": "missing"}),
                mapping.bindings[1],
            )
        }
    )
    payload = payload.model_copy(update={"mapping": revision_of(changed)})
    store = LocalArtifactStore(tmp_path)
    result = import_measurement(
        payload.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "bad"),
        specimen=specimen,
        mapping=changed,
        problem=problem,
        store=store,
    )
    assert "measurement.invalid_mapping" in codes(result)
    assert not store.list_artifacts()


def test_raw_ingest_and_processing_preserve_lineage_and_scope(tmp_path: Path) -> None:
    specimen, mapping, problem, payload = fixture()
    store = LocalArtifactStore(tmp_path)
    raw = json.dumps(payload.model_dump(mode="json"), indent=3).encode()
    original = import_measurement(
        raw,
        id=EntityId("measurement_dataset", "d1"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    ).unwrap()
    assert store.read_bytes(original.raw_artifact, "source.json") == raw
    assert original.raw_artifact == original.data_artifact
    assert original.observations[0].kind == "optical_power"
    assert len(original.observations) == 1
    assert original.observations[0].standard_deviation is None
    assert set(upstream(store, original.raw_artifact).revisions) == {
        revision_of(specimen),
        specimen.design,
        revision_of(mapping),
    }
    processed = scale_power(original, 2.0, store=store).unwrap()
    assert processed.raw_artifact == original.raw_artifact
    assert processed.data_artifact != original.data_artifact
    assert processed.parent == revision_of(original)
    assert processed.observations[0].values == (2.0, 2.4)
    assert processed.observations[0].standard_deviation is None
    assert original.raw_artifact in upstream(store, processed.data_artifact).artifacts
    assert revision_of(original) in upstream(store, processed.data_artifact).revisions


def test_bad_references_and_partial_channels(tmp_path: Path) -> None:
    specimen, mapping, problem, payload = fixture()
    store = LocalArtifactStore(tmp_path)
    bad_specimen = payload.model_copy(
        update={
            "specimen": RevisionRef(EntityId("specimen", "other"), "sha256:" + "b" * 64)
        }
    )
    result = import_measurement(
        bad_specimen.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert "measurement.specimen_mismatch" in codes(result)
    bad_design = payload.model_copy(
        update={
            "design": RevisionRef(EntityId("design", "other"), "sha256:" + "b" * 64)
        }
    )
    result = import_measurement(
        bad_design.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert "measurement.design_mismatch" in codes(result)
    unrelated_problem = problem.model_copy(update={"design": None})
    result = import_measurement(
        payload.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=unrelated_problem,
        store=store,
    )
    assert {"measurement.problem_mismatch", "measurement.design_mismatch"} <= codes(
        result
    )
    bad_mapping = payload.model_copy(
        update={
            "mapping": RevisionRef(
                EntityId("view_mapping", "other"), "sha256:" + "b" * 64
            )
        }
    )
    result = import_measurement(
        bad_mapping.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert "measurement.mapping_mismatch" in codes(result)
    bad_channel = payload.model_copy(
        update={
            "observations": (
                payload.observations[0].model_copy(
                    update={"output_channel": "unknown"}
                ),
            )
        }
    )
    result = import_measurement(
        bad_channel.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert "measurement.unknown_channel" in codes(result)
    unchannelled = mapping.model_copy(
        update={
            "bindings": tuple(
                binding.model_copy(update={"measurement_channel": None})
                for binding in mapping.bindings
            )
        }
    )
    unchannelled_payload = payload.model_copy(
        update={"mapping": revision_of(unchannelled)}
    )
    result = import_measurement(
        unchannelled_payload.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=unchannelled,
        problem=problem,
        store=store,
    )
    assert "measurement.invalid_mapping" in codes(result)
    assert store.list_artifacts() == ()


def test_invalid_json_units_uncertainty_and_corrupt_raw(tmp_path: Path) -> None:
    specimen, mapping, problem, payload = fixture()
    store = LocalArtifactStore(tmp_path)
    invalid = import_measurement(
        b"{oops",
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert "measurement.invalid_json" in codes(invalid)
    data = payload.model_dump(mode="json")
    data["axis"]["unit"] = "GHz"
    data["observations"][0]["standard_deviation"] = [0.0]
    bad = import_measurement(
        json.dumps(data).encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    )
    assert {"measurement.axis_unit", "measurement.uncertainty"} <= codes(bad)
    raw = payload.model_dump_json().encode()
    dataset = import_measurement(
        raw,
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    ).unwrap()
    path = (
        tmp_path
        / "artifacts"
        / "measurement_raw"
        / dataset.raw_artifact.digest.partition(":")[2]
        / "files"
        / "source.json"
    )
    path.write_bytes(b"changed")
    assert {d.code for d in store.verify(dataset.raw_artifact)} == {
        "artifact.file_corrupt"
    }
    assert "artifact.file_corrupt" in codes(scale_power(dataset, 2.0, store=store))


def test_processing_rejects_forged_dataset_metadata(tmp_path: Path) -> None:
    specimen, mapping, problem, payload = fixture()
    store = LocalArtifactStore(tmp_path)
    dataset = import_measurement(
        payload.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "d"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    ).unwrap()
    forged = dataset.model_copy(
        update={
            "observations": (
                dataset.observations[0].model_copy(update={"values": (5.0, 6.0)}),
            )
        }
    )
    assert "measurement.source_mismatch" in codes(scale_power(forged, 2.0, store=store))
    assert store.list_artifacts("measurement_processed") == ()


def test_explicit_zero_uncertainty_survives_processing(tmp_path: Path) -> None:
    specimen, mapping, problem, payload = fixture()
    observation = payload.observations[0].model_copy(
        update={"standard_deviation": (0.0, 0.0)}
    )
    payload = payload.model_copy(update={"observations": (observation,)})
    store = LocalArtifactStore(tmp_path)
    dataset = import_measurement(
        payload.model_dump_json().encode(),
        id=EntityId("measurement_dataset", "zero"),
        specimen=specimen,
        mapping=mapping,
        problem=problem,
        store=store,
    ).unwrap()
    processed = scale_power(dataset, 3.0, store=store).unwrap()
    assert processed.observations[0].standard_deviation == (0.0, 0.0)
