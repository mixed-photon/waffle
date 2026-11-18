"""Publishing a GDS and its sidecar as one immutable artifact, and reading it back.

The artifact (kind `layout_gds`) holds `<layout>.gds` and `<layout>.gds.waffle.json`, so the
pair cannot be separated or half-published. Its provenance links the layout revision (and,
when the sidecar names them, the design, technology, and implementation revisions) as
inputs of the producing operation `layout.export_gds`, run by the sidecar's producer.
"""

from __future__ import annotations

from waffle.artifacts.store import ArtifactError, ArtifactStore
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import load_record, to_canonical_json
from waffle.contracts.foundation.result import Result
from waffle.contracts.results.artifact import ArtifactRef
from waffle.contracts.results.provenance import InputLink, OperationInfo
from waffle.layout.formats.sidecar import SIDECAR_SUFFIX, GdsPackage, GdsSidecar, verify_sidecar

ARTIFACT_KIND = "layout_gds"
OPERATION = "layout.export_gds"


def _operation(sidecar: GdsSidecar) -> OperationInfo:
    producer = sidecar.producer
    return OperationInfo(
        name=OPERATION,
        version=producer.version,
        parameters_digest=content_hash(producer.model_dump(mode="json")),
        environment={
            "backend": producer.name,
            **{f"package.{name}": version for name, version in producer.upstream.items()},
        },
    )


def _inputs(sidecar: GdsSidecar) -> list[InputLink]:
    named = {
        "layout": sidecar.layout,
        "design": sidecar.design,
        "technology": sidecar.technology,
        "implementation": sidecar.implementation,
    }
    return [InputLink(role=role, target=ref) for role, ref in named.items() if ref is not None]


def publish_package(store: ArtifactStore, package: GdsPackage) -> Result[ArtifactRef]:
    """Publish `package` after checking the sidecar describes the bytes (otherwise nothing is
    written). Publishing the same package again returns the same reference."""
    sidecar = package.sidecar
    found = verify_sidecar(package.gds, sidecar)
    if has_errors(found):
        return Result(None, tuple(found))
    name = sidecar.layout.entity.name
    with store.attempt(_operation(sidecar), _inputs(sidecar)) as session:
        writer = session.new_artifact(
            ARTIFACT_KIND,
            {"layout": str(sidecar.layout), "top": sidecar.top, "sha256": sidecar.sha256},
        )
        writer.write_bytes(f"{name}.gds", package.gds, "application/octet-stream")
        writer.write_bytes(
            f"{name}.gds{SIDECAR_SUFFIX}",
            to_canonical_json(sidecar).encode("utf-8"),
            "application/json",
        )
        return Result.of(writer.commit())


def load_package(store: ArtifactStore, ref: ArtifactRef) -> Result[GdsPackage]:
    """Read a published `layout_gds` artifact, checking its files and the sidecar."""

    def fail(code: str, message: str) -> Result[GdsPackage]:
        return Result(
            None,
            (Diagnostic(severity=Severity.ERROR, code=code, message=message, subject=str(ref)),),
        )

    try:
        manifest = store.inspect(ref)
    except ArtifactError as exc:
        return Result(None, exc.diagnostics)
    if manifest.kind != ARTIFACT_KIND:
        return fail("gds.artifact.kind", f"expected a {ARTIFACT_KIND!r} artifact, got {ref.kind!r}")
    if has_errors(integrity := store.verify(ref)):
        return Result(None, integrity)
    sidecar_files = [f.name for f in manifest.files if f.name.endswith(SIDECAR_SUFFIX)]
    gds_file = sidecar_files[0][: -len(SIDECAR_SUFFIX)] if len(sidecar_files) == 1 else ""
    if manifest.file(gds_file) is None:
        return fail("gds.artifact.files", "artifact must hold one GDS file and its sidecar")
    loaded = load_record(store.read_bytes(ref, sidecar_files[0]).decode("utf-8"), GdsSidecar)
    if loaded.value is None:
        return Result(None, loaded.diagnostics)
    gds = store.read_bytes(ref, gds_file)
    found = verify_sidecar(gds, loaded.value)
    if has_errors(found):
        return Result(None, tuple(found))
    return Result.of(GdsPackage(gds, loaded.value), found)
