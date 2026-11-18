from __future__ import annotations

from pathlib import Path

import pytest

from waffle.artifacts.local import LocalArtifactStore
from waffle.contracts.component.examples import TECHNOLOGY, phase_shifter
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.provenance import OperationInfo
from waffle.layout.formats.publish import ARTIFACT_KIND, OPERATION, load_package, publish_package
from waffle.layout.formats.sidecar import GdsPackage, Producer, build_sidecar
from waffle.layout.formats.testing import gds_bytes

LAYOUT = nested_layout()
GDS = gds_bytes(LAYOUT)
PRODUCER = Producer(name="fake", version="1.0.0", upstream={"gdsfactory": "9.51.0"})
EXAMPLE = phase_shifter()
OTHER_OPERATION = OperationInfo(name="demo.op", version="1", parameters_digest=content_hash({}))


@pytest.fixture
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "store")


def package() -> GdsPackage:
    sidecar = build_sidecar(
        LAYOUT,
        GDS,
        PRODUCER,
        design=EXAMPLE.design_ref,
        technology=TECHNOLOGY,
        implementation=revision_of(EXAMPLE.implementation),
    )
    return GdsPackage(GDS, sidecar)


def test_pair_is_one_immutable_artifact(store: LocalArtifactStore) -> None:
    ref = publish_package(store, package()).unwrap()
    assert ref.kind == ARTIFACT_KIND == "layout_gds"
    manifest = store.inspect(ref)
    assert [f.name for f in manifest.files] == ["nested.gds", "nested.gds.waffle.json"]
    assert manifest.metadata["top"] == "top"
    assert store.verify(ref) == ()
    assert publish_package(store, package()).unwrap() == ref
    assert store.list_artifacts() == (ref,)


def test_provenance_links_layout_design_and_technology(store: LocalArtifactStore) -> None:
    ref = publish_package(store, package()).unwrap()
    (record,) = store.provenance_of(ref)
    assert record.operation.name == OPERATION
    assert record.operation.version == "1.0.0"
    assert record.operation.environment["package.gdsfactory"] == "9.51.0"
    assert {link.role: link.target for link in record.inputs} == {
        "layout": revision_of(LAYOUT),
        "design": EXAMPLE.design_ref,
        "technology": TECHNOLOGY,
        "implementation": revision_of(EXAMPLE.implementation),
    }


def test_published_pair_loads_and_verifies(store: LocalArtifactStore) -> None:
    ref = publish_package(store, package()).unwrap()
    assert load_package(store, ref).unwrap() == package()


def test_inconsistent_pair_is_not_published(store: LocalArtifactStore) -> None:
    broken = GdsPackage(GDS + b"\0\0", package().sidecar)
    result = publish_package(store, broken)
    assert [d.code for d in result.diagnostics] == ["gds.sidecar.size_mismatch"]
    assert store.list_artifacts() == ()


def test_corrupted_stored_file_is_detected(store: LocalArtifactStore) -> None:
    ref = publish_package(store, package()).unwrap()
    path = store._artifact_dir(ref) / "files" / "nested.gds"
    path.chmod(0o644)
    path.write_bytes(GDS[:-1] + b"\x01")
    result = load_package(store, ref)
    assert result.value is None
    assert [d.code for d in result.diagnostics] == ["artifact.file_corrupt"]


def test_other_artifacts_are_rejected(store: LocalArtifactStore) -> None:
    missing = ArtifactRef(ARTIFACT_KIND, "sha256:" + "0" * 64)
    assert [d.code for d in load_package(store, missing).diagnostics] == ["artifact.not_found"]
    with store.attempt(OTHER_OPERATION) as session:
        writer = session.new_artifact("dataset")
        writer.write_bytes("a.bin", b"x")
        other = writer.commit()
    assert [d.code for d in load_package(store, other).diagnostics] == ["gds.artifact.kind"]
