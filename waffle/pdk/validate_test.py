from __future__ import annotations

from waffle.contracts.foundation.records import revision_of
from waffle.contracts.technology.pdk import Pdk
from waffle.pdk.testing.synthetic import synthetic_soi, synthetic_soi_pdk
from waffle.pdk.validate import validate_pdk


def codes(pdk: Pdk, version: str | None = None) -> list[str]:
    return [d.code for d in validate_pdk(pdk, version)]


def test_synthetic_pdk_is_valid_and_compatible() -> None:
    assert validate_pdk(synthetic_soi_pdk(), "0.0.0") == []
    assert validate_pdk(synthetic_soi_pdk()) == []


def test_compatibility_is_checked_only_when_a_version_is_given() -> None:
    assert codes(synthetic_soi_pdk(), "1.0.0") == ["pdk.incompatible_waffle"]
    assert codes(synthetic_soi_pdk()) == []
    found = validate_pdk(synthetic_soi_pdk(), "2.0")
    assert found[0].location == ("waffle",)
    assert "needs Waffle >=0.0.0,<1.0.0, found 2.0" in found[0].message


def test_shipped_technologies_must_match_the_manifest_pins() -> None:
    pdk = synthetic_soi_pdk()
    edited = synthetic_soi().model_copy(update={"metadata": {"notice": "edited"}})
    unpinned = Pdk(manifest=pdk.manifest, technologies=(*pdk.technologies, edited))
    assert codes(unpinned) == ["pdk.manifest_mismatch"]
    assert str(revision_of(edited)) in validate_pdk(unpinned)[0].message
    swapped = Pdk(manifest=pdk.manifest, technologies=(edited,))
    assert codes(swapped) == ["pdk.manifest_mismatch", "pdk.manifest_mismatch"]


def test_duplicate_technology_is_reported() -> None:
    pdk = synthetic_soi_pdk()
    doubled = Pdk(manifest=pdk.manifest, technologies=(*pdk.technologies, *pdk.technologies))
    assert codes(doubled) == ["pdk.duplicate_technology"]


def test_technology_problems_are_located_inside_the_pdk() -> None:
    tech = synthetic_soi().model_copy(update={"dbu_pm": 3000})
    pdk = synthetic_soi_pdk()
    broken = Pdk(
        manifest=pdk.manifest.model_copy(update={"technologies": (revision_of(tech),)}),
        technologies=(tech,),
    )
    found = validate_pdk(broken)
    assert [d.code for d in found] == ["technology.grid_mismatch"]
    assert found[0].location == ("technologies", 0, "manufacturing_grid_pm")
