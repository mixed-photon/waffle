from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from waffle.artifacts.environment import capture_environment
from waffle.artifacts.lineage import dependents, direct_inputs, upstream
from waffle.artifacts.local import LocalArtifactStore
from waffle.artifacts.store import ArtifactStore
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.provenance import InputLink, OperationInfo, ProvenanceRecord

DESIGN = RevisionRef.of(EntityId("component", "mmi_2x2"), {"width": 0.5})


def operation(name: str, **parameters: object) -> OperationInfo:
    return OperationInfo(
        name=name,
        version="1.2",
        parameters_digest=content_hash(parameters),
        environment=capture_environment(["numpy"]),
    )


def make(
    store: ArtifactStore,
    kind: str,
    op: OperationInfo,
    inputs: list[InputLink],
    value: float,
) -> ArtifactRef:
    with store.attempt(op, inputs) as session:
        writer = session.new_artifact(kind, {"value": value})
        writer.write_array(f"{kind}.npy", np.full(4, value))
        return writer.commit()


def link(target: ArtifactRef | RevisionRef, role: str = "input") -> InputLink:
    return InputLink(role=role, target=target)


@pytest.fixture
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "store")


@pytest.fixture
def chain(store: LocalArtifactStore) -> dict[str, ArtifactRef]:
    """raw -> (simulate) -> fitting; validation -> (extract) -> model <- (fit) <- fitting."""
    raw = make(store, "dataset", operation("ingest"), [link(DESIGN, "design")], 1.0)
    fitting = make(store, "dataset", operation("preprocess"), [link(raw)], 2.0)
    validation = make(store, "dataset", operation("simulate"), [link(DESIGN, "design")], 3.0)
    model = make(
        store,
        "model",
        operation("fit", order=3),
        [link(fitting, "fitting_data"), link(validation, "validation_data")],
        4.0,
    )
    return {"raw": raw, "fitting": fitting, "validation": validation, "model": model}


# Criterion 5: published models and datasets are traceable to inputs and operations


def test_model_is_traceable_to_inputs_and_producing_operation(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    [record] = store.provenance_of(chain["model"])
    assert record.artifact == chain["model"]
    assert (record.operation.name, record.operation.version) == ("fit", "1.2")
    assert record.operation.parameters_digest == content_hash({"order": 3})
    assert record.operation.environment["package.numpy"]
    assert {(i.role, i.target) for i in record.inputs} == {
        ("fitting_data", chain["fitting"]),
        ("validation_data", chain["validation"]),
    }
    attempt = store.get_attempt(record.attempt_id)
    assert attempt.outcome is not None and attempt.outcome.produced == (chain["model"],)
    assert attempt.start.inputs == record.inputs
    assert [link.role for link in direct_inputs(store, chain["model"])] == [
        "fitting_data",
        "validation_data",
    ]


def test_fitting_and_validation_data_stay_distinguishable(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    by_role = {i.role: i.target for i in direct_inputs(store, chain["model"])}
    assert by_role["fitting_data"] == chain["fitting"]
    assert by_role["validation_data"] == chain["validation"]


def test_upstream_lineage_is_transitive_and_reaches_record_revisions(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    lineage = upstream(store, chain["model"])
    assert lineage.complete and lineage.diagnostics == ()
    assert lineage.artifacts == tuple(sorted([chain["raw"], chain["fitting"], chain["validation"]]))
    assert lineage.revisions == (DESIGN,)
    assert {r.operation.name for r in lineage.records} == {
        "fit",
        "preprocess",
        "ingest",
        "simulate",
    }
    assert upstream(store, chain["raw"]).artifacts == ()


def test_dependents_direct_and_transitive(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    assert dependents(store, chain["raw"]) == (chain["fitting"],)
    assert dependents(store, chain["raw"], transitive=True) == tuple(
        sorted([chain["fitting"], chain["model"]])
    )
    assert dependents(store, chain["model"], transitive=True) == ()


def test_dependents_of_a_record_revision(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    assert dependents(store, DESIGN) == tuple(sorted([chain["raw"], chain["validation"]]))
    assert dependents(store, DESIGN, transitive=True) == tuple(sorted(chain.values()))


def test_missing_upstream_artifact_is_reported_clearly(
    store: LocalArtifactStore, chain: dict[str, ArtifactRef]
) -> None:
    shutil.rmtree(store._artifact_dir(chain["fitting"]))
    lineage = upstream(store, chain["model"])
    assert not lineage.complete
    [problem] = lineage.diagnostics
    assert problem.code == "lineage.missing_artifact"
    assert problem.subject == str(chain["fitting"])
    assert str(chain["model"]) in problem.message
    assert chain["raw"] not in lineage.artifacts  # cannot walk past the missing artifact
    assert chain["validation"] in lineage.artifacts


def test_missing_root_is_reported(store: LocalArtifactStore) -> None:
    lineage = upstream(store, ArtifactRef("model", "sha256:" + "2" * 64))
    assert [d.code for d in lineage.diagnostics] == ["lineage.missing_artifact"]


def test_same_content_from_different_attempts_keeps_every_producer(
    store: LocalArtifactStore,
) -> None:
    first_input = make(store, "dataset", operation("a"), [], 1.0)
    second_input = make(store, "dataset", operation("b"), [], 2.0)
    one = make(store, "model", operation("fit"), [link(first_input)], 9.0)
    two = make(store, "model", operation("fit"), [link(second_input)], 9.0)
    assert one == two
    records = store.provenance_of(one)
    assert len(records) == 2 and records[0].attempt_id != records[1].attempt_id
    assert {i.target for i in direct_inputs(store, one)} == {first_input, second_input}
    assert upstream(store, one).artifacts == tuple(sorted([first_input, second_input]))


def test_lineage_terminates_on_cycles(store: LocalArtifactStore) -> None:
    a = make(store, "dataset", operation("a"), [], 1.0)
    b = make(store, "dataset", operation("b"), [link(a)], 2.0)
    forged = ProvenanceRecord(
        artifact=a,
        attempt_id="20260101T000000Z-0123456789ab",
        operation=operation("forged"),
        inputs=(link(b),),
        recorded_at=store.provenance_of(a)[0].recorded_at,
    )
    path = store._provenance_dir(a) / f"{forged.attempt_id}.json"
    path.write_text(forged.model_dump_json())
    assert upstream(store, a).artifacts == (b,)
    assert upstream(store, b).artifacts == (a,)
    assert dependents(store, a, transitive=True) == (b,)


def test_artifact_without_provenance_is_flagged(store: LocalArtifactStore) -> None:
    ref = make(store, "dataset", operation("a"), [], 1.0)
    shutil.rmtree(store._provenance_dir(ref))
    [warning] = upstream(store, ref).diagnostics
    assert warning.code == "lineage.no_provenance"
