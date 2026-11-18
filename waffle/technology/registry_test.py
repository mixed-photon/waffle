from __future__ import annotations

from pathlib import Path

import pytest

from waffle.contracts.foundation.diagnostics import has_errors
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.technology.pdk import Access, Pdk
from waffle.pdk.directory import validate_pdk_directory, write_pdk
from waffle.pdk.testing.synthetic import TECHNOLOGY_ID, synthetic_soi, synthetic_soi_pdk
from waffle.technology.registry import ENTRY_POINT_GROUP, PdkRegistry


class FakeEntryPoint:
    """Stands in for an installed distribution's entry point."""

    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def codes(diagnostics) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.code for d in diagnostics]


def test_discovery_loads_each_entry_point_through_the_shared_validation() -> None:
    registry = PdkRegistry(waffle_version="0.0.0")
    assert registry.discover([FakeEntryPoint("synthetic", synthetic_soi_pdk)]) == []
    assert [p.manifest.id for p in registry.pdks] == [EntityId("pdk", "synthetic_soi")]
    ref = revision_of(synthetic_soi())
    assert registry.resolve(ref).unwrap() == synthetic_soi()
    assert registry.revisions(TECHNOLOGY_ID) == (ref,)


def test_discovery_reports_broken_entry_points_and_keeps_going() -> None:
    registry = PdkRegistry(waffle_version="0.0.0")
    found = registry.discover(
        [
            FakeEntryPoint("raises_on_import", ImportError("no module named vendor_pdk")),
            FakeEntryPoint("not_callable", 42),
            FakeEntryPoint("wrong_type", lambda: "a string"),
            FakeEntryPoint("raises_on_call", lambda: 1 / 0),
            FakeEntryPoint("good", synthetic_soi_pdk),
        ]
    )
    assert codes(found) == [
        "pdk.entry_point_failed",
        "pdk.entry_point_invalid",
        "pdk.entry_point_invalid",
        "pdk.entry_point_failed",
    ]
    assert found[0].subject == f"{ENTRY_POINT_GROUP}:raises_on_import"
    assert "no module named vendor_pdk" in found[0].message
    assert registry.resolve(revision_of(synthetic_soi())).ok


def test_default_discovery_uses_installed_entry_points_without_error() -> None:
    assert PdkRegistry().discover() == []  # nothing installed advertises waffle.pdks here


@pytest.mark.parametrize("access", list(Access))
def test_directory_route_gives_the_same_result_as_the_entry_point_route(
    tmp_path: Path, access: Access
) -> None:
    synthetic = synthetic_soi_pdk()
    pdk = Pdk(
        manifest=synthetic.manifest.model_copy(update={"access": access}),
        technologies=synthetic.technologies,
    )
    directory = tmp_path / "pdk"
    assert write_pdk(pdk, directory) == []
    assert validate_pdk_directory(directory, "0.0.0") == []

    from_directory = PdkRegistry(waffle_version="0.0.0")
    from_entry_point = PdkRegistry(waffle_version="0.0.0")
    assert from_directory.add_directory(directory) == []
    assert from_entry_point.discover([FakeEntryPoint("s", lambda: pdk)]) == []

    (ref,) = pdk.manifest.technologies
    for registry in (from_directory, from_entry_point):
        assert registry.pdks == (pdk,)
        assert registry.pdks[0].manifest.access is access
        assert registry.revisions(TECHNOLOGY_ID) == (ref,)
        assert registry.resolve(ref).unwrap() == synthetic_soi()


@pytest.mark.parametrize("access", list(Access))
def test_incompatible_waffle_version_is_refused_for_both_routes(
    tmp_path: Path, access: Access
) -> None:
    synthetic = synthetic_soi_pdk()
    pdk = Pdk(
        manifest=synthetic.manifest.model_copy(update={"access": access}),
        technologies=synthetic.technologies,
    )
    assert write_pdk(pdk, tmp_path / "pdk") == []  # requires >=0.0.0,<1.0.0
    registry = PdkRegistry(waffle_version="1.2.0")
    assert codes(registry.add_directory(tmp_path / "pdk")) == ["pdk.incompatible_waffle"]
    assert codes(registry.discover([FakeEntryPoint("s", lambda: pdk)])) == [
        "pdk.incompatible_waffle"
    ]
    assert registry.pdks == ()
    assert codes(registry.resolve(revision_of(synthetic_soi())).diagnostics) == [
        "technology.unknown"
    ]


def test_invalid_technology_blocks_registration() -> None:
    tech = synthetic_soi().model_copy(update={"manufacturing_grid_pm": 1500})
    pdk = synthetic_soi_pdk()
    broken = Pdk(
        manifest=pdk.manifest.model_copy(update={"technologies": (revision_of(tech),)}),
        technologies=(tech,),
    )
    registry = PdkRegistry(waffle_version="0.0.0")
    found = registry.add(broken)
    assert codes(found) == ["technology.grid_mismatch"]
    assert found[0].location == ("technologies", 0, "manufacturing_grid_pm")
    assert registry.pdks == ()


def test_missing_revision_lists_what_is_available() -> None:
    registry = PdkRegistry(waffle_version="0.0.0")
    registry.add(synthetic_soi_pdk())
    stale = revision_of(synthetic_soi().model_copy(update={"metadata": {"notice": "older"}}))
    result = registry.resolve(stale)
    assert result.value is None
    assert codes(result.diagnostics) == ["technology.revision_unavailable"]
    message = result.diagnostics[0].message
    assert str(stale) in message
    assert str(revision_of(synthetic_soi())) in message


def test_unknown_technology_is_distinguished_from_unknown_revision() -> None:
    registry = PdkRegistry(waffle_version="0.0.0")
    registry.add(synthetic_soi_pdk())
    other = revision_of(synthetic_soi().model_copy(update={"id": EntityId("technology", "x")}))
    assert codes(registry.resolve(other).diagnostics) == ["technology.unknown"]


def test_adding_the_same_pdk_twice_is_idempotent_but_a_different_one_is_a_duplicate() -> None:
    registry = PdkRegistry(waffle_version="0.0.0")
    assert registry.add(synthetic_soi_pdk()) == []
    assert registry.add(synthetic_soi_pdk()) == []
    assert len(registry.pdks) == 1
    pdk = synthetic_soi_pdk()
    newer = Pdk(
        manifest=pdk.manifest.model_copy(update={"version": "1.1.0"}),
        technologies=pdk.technologies,
    )
    found = registry.add(newer)
    assert codes(found) == ["pdk.duplicate"]
    assert has_errors(found)
    assert registry.pdks[0].manifest.version == "1.0.0"


def test_directory_errors_are_diagnostics(tmp_path: Path) -> None:
    assert codes(PdkRegistry().add_directory(tmp_path / "missing")) == ["pdk.directory_missing"]


@pytest.mark.parametrize("version", ["0.0.0", "0.9.9"])
def test_compatible_versions_load(version: str) -> None:
    assert PdkRegistry(waffle_version=version).add(synthetic_soi_pdk()) == []
