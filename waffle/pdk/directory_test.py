from __future__ import annotations

from pathlib import Path

from waffle.contracts.foundation.records import revision_of
from waffle.contracts.technology.pdk import Pdk
from waffle.pdk.directory import (
    MANIFEST_FILE,
    TECHNOLOGY_DIR,
    read_pdk,
    validate_pdk_directory,
    write_pdk,
)
from waffle.pdk.testing.synthetic import synthetic_soi, synthetic_soi_pdk


def codes(diagnostics) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.code for d in diagnostics]


def test_export_then_import_gives_the_identical_pdk(tmp_path: Path) -> None:
    pdk = synthetic_soi_pdk()
    assert write_pdk(pdk, tmp_path) == []
    loaded = read_pdk(tmp_path).unwrap()
    assert loaded == pdk
    assert revision_of(loaded.manifest) == revision_of(pdk.manifest)
    assert [revision_of(t) for t in loaded.technologies] == [revision_of(synthetic_soi())]


def test_layout_is_one_canonical_json_file_per_record(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    assert (tmp_path / MANIFEST_FILE).read_text().startswith('{"access":"public"')
    names = [p.name for p in (tmp_path / TECHNOLOGY_DIR).iterdir()]
    digest = revision_of(synthetic_soi()).digest.removeprefix("sha256:")[:12]
    assert names == [f"synthetic_soi.{digest}.json"]


def test_writing_is_deterministic(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path / "a")
    write_pdk(synthetic_soi_pdk(), tmp_path / "b")
    files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.json"))
    assert files
    for file in files:
        assert (tmp_path / "a" / file).read_bytes() == (tmp_path / "b" / file).read_bytes()


def test_invalid_pdk_is_not_written(tmp_path: Path) -> None:
    tech = synthetic_soi().model_copy(update={"manufacturing_grid_pm": 1500})
    pdk = synthetic_soi_pdk()
    broken = Pdk(
        manifest=pdk.manifest.model_copy(update={"technologies": (revision_of(tech),)}),
        technologies=(tech,),
    )
    assert codes(write_pdk(broken, tmp_path / "out")) == ["technology.grid_mismatch"]
    assert not (tmp_path / "out").exists()


def test_existing_pdk_is_not_overwritten(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    assert codes(write_pdk(synthetic_soi_pdk(), tmp_path)) == ["pdk.directory_exists"]


def test_missing_directory_and_manifest(tmp_path: Path) -> None:
    assert codes(read_pdk(tmp_path / "nope").diagnostics) == ["pdk.directory_missing"]
    assert codes(read_pdk(tmp_path).diagnostics) == ["pdk.manifest_missing"]


def test_manifest_without_technology_files(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    for file in (tmp_path / TECHNOLOGY_DIR).iterdir():
        file.unlink()
    assert codes(read_pdk(tmp_path).diagnostics) == ["pdk.no_technologies"]


def test_corrupt_files_are_reported_with_their_path(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    technology_file = next((tmp_path / TECHNOLOGY_DIR).iterdir())
    technology_file.write_text("{not json")
    result = read_pdk(tmp_path)
    assert result.value is None
    assert codes(result.diagnostics) == ["record.invalid_json"]
    assert result.diagnostics[0].subject == str(technology_file)


def test_validate_directory_is_the_cli_entry(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    assert validate_pdk_directory(tmp_path, "0.5.0") == []
    assert codes(validate_pdk_directory(tmp_path, "3.0.0")) == ["pdk.incompatible_waffle"]
    assert codes(validate_pdk_directory(tmp_path / "missing")) == ["pdk.directory_missing"]


def test_tampered_technology_is_caught_by_manifest_pins(tmp_path: Path) -> None:
    write_pdk(synthetic_soi_pdk(), tmp_path)
    technology_file = next((tmp_path / TECHNOLOGY_DIR).iterdir())
    technology_file.write_text(
        technology_file.read_text().replace("Waveguide core", "Waveguide core, edited")
    )
    assert codes(validate_pdk_directory(tmp_path, "0.5.0")) == [
        "pdk.manifest_mismatch",
        "pdk.manifest_mismatch",
    ]
