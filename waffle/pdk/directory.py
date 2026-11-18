"""Read, write, and validate a PDK stored as a directory.

Layout (canonical JSON, one record per file)::

    <directory>/pdk.json                           the manifest
    <directory>/technologies/<name>.<hex12>.json   one file per technology revision

`<hex12>` is the first 12 hex digits of the revision digest; file names are not trusted:
reading uses file contents and the manifest decides what belongs.
"""

from __future__ import annotations

from pathlib import Path

from waffle import __version__
from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.result import Result
from waffle.contracts.technology.pdk import Pdk, PdkManifest
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.pdk.validate import validate_pdk

MANIFEST_FILE = "pdk.json"
TECHNOLOGY_DIR = "technologies"


def write_pdk(pdk: Pdk, directory: Path) -> list[Diagnostic]:
    """Write `pdk` into `directory` (created if needed) after validating it.

    Nothing is written if validation finds an error, or `directory` already holds a PDK
    (`pdk.directory_exists`).
    """
    found = validate_pdk(pdk)
    manifest_path = directory / MANIFEST_FILE
    if manifest_path.exists():
        found.append(_error("pdk.directory_exists", f"{manifest_path} already exists", directory))
    if has_errors(found):
        return found
    (directory / TECHNOLOGY_DIR).mkdir(parents=True, exist_ok=True)
    for technology in pdk.technologies:
        digest = revision_of(technology).digest.removeprefix("sha256:")[:12]
        path = directory / TECHNOLOGY_DIR / f"{technology.id.name}.{digest}.json"
        path.write_text(to_canonical_json(technology) + "\n", encoding="utf-8")
    manifest_path.write_text(to_canonical_json(pdk.manifest) + "\n", encoding="utf-8")
    return found


def read_pdk(directory: Path) -> Result[Pdk]:
    """Load the PDK in `directory`. Content checks are `validate_pdk`'s job, not this one's.

    Codes: `pdk.directory_missing`, `pdk.manifest_missing`, `pdk.no_technologies`, plus the
    `record.*` diagnostics of any unreadable file, with the file as `subject`.
    """
    manifest_path = directory / MANIFEST_FILE
    if not directory.is_dir():
        return Result(
            None, (_error("pdk.directory_missing", f"{directory} is not a directory", directory),)
        )
    if not manifest_path.is_file():
        return Result(
            None, (_error("pdk.manifest_missing", f"{manifest_path} does not exist", directory),)
        )
    found: list[Diagnostic] = []
    manifest = _load(manifest_path, PdkManifest, found)
    technologies = [
        t
        for path in sorted((directory / TECHNOLOGY_DIR).glob("*.json"))
        if (t := _load(path, TechnologyRevision, found)) is not None
    ]
    if manifest is None or has_errors(found):
        return Result(None, tuple(found))
    if not technologies:
        found.append(_error("pdk.no_technologies", f"{directory} holds no technologies", directory))
        return Result(None, tuple(found))
    return Result.of(Pdk(manifest=manifest, technologies=tuple(technologies)), found)


def validate_pdk_directory(directory: Path, waffle_version: str = __version__) -> list[Diagnostic]:
    """Read and validate a PDK directory, including compatibility with `waffle_version`.

    The entry point for a CLI command: no error in the list means the PDK is usable.
    """
    result = read_pdk(directory)
    if result.value is None:
        return list(result.diagnostics)
    return [*result.diagnostics, *validate_pdk(result.value, waffle_version)]


def _load[R: (PdkManifest, TechnologyRevision)](
    path: Path, expected: type[R], found: list[Diagnostic]
) -> R | None:
    result = load_record(path.read_text(encoding="utf-8"), expected)
    found.extend(d.model_copy(update={"subject": str(path)}) for d in result.diagnostics)
    return result.value


def _error(code: str, message: str, path: Path) -> Diagnostic:
    return Diagnostic(severity=Severity.ERROR, code=code, message=message, subject=str(path))
