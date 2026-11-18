from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Sellmeier,
    Tabulated,
    WavelengthRange,
)
from waffle.contracts.technology.pdk import Pdk, PdkManifest
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.pdk.testing.synthetic import SYNTHETIC_NOTICE, synthetic_soi, synthetic_soi_pdk


def test_technology_round_trips_with_its_revision_digest() -> None:
    tech = synthetic_soi()
    loaded = load_record(to_canonical_json(tech), TechnologyRevision).unwrap()
    assert loaded == tech
    assert revision_of(loaded) == revision_of(tech)
    assert revision_of(tech).entity == EntityId("technology", "synthetic_soi")


def test_manifest_round_trips_and_is_marked_synthetic() -> None:
    manifest = synthetic_soi_pdk().manifest
    loaded = load_record(to_canonical_json(manifest), PdkManifest).unwrap()
    assert revision_of(loaded) == revision_of(manifest)
    assert manifest.metadata["notice"] == SYNTHETIC_NOTICE
    assert synthetic_soi().metadata["notice"] == SYNTHETIC_NOTICE


def test_unknown_fields_and_wrong_discriminators_are_rejected() -> None:
    payload = synthetic_soi().model_dump(mode="json")
    result = load_record({**payload, "surprise": 1}, TechnologyRevision)
    assert [d.code for d in result.diagnostics] == ["record.invalid"]
    payload["stack"]["levels"][0]["pattern"] = {"type": "etched"}
    assert not load_record(payload, TechnologyRevision).ok


def test_technology_requires_a_valid_grid() -> None:
    payload = synthetic_soi().model_dump(mode="json")
    assert not load_record({**payload, "dbu_pm": 0}, TechnologyRevision).ok
    assert not load_record({**payload, "manufacturing_grid_pm": -5}, TechnologyRevision).ok


def test_constant_index_must_be_physical() -> None:
    assert ConstantIndex(n=1.0).k == 0.0
    for bad in ({"n": 0.0}, {"n": 1.0, "k": -0.1}, {"n": float("nan")}):
        with pytest.raises(ValidationError):
            ConstantIndex(**bad)


def test_sellmeier_terms_must_pair_up_and_be_non_negative() -> None:
    with pytest.raises(ValidationError, match="same length"):
        Sellmeier(b=(1.0, 2.0), c_um2=(0.1,))
    with pytest.raises(ValidationError, match="non-negative"):
        Sellmeier(b=(1.0,), c_um2=(-0.1,))


def test_tabulated_samples_must_be_consistent() -> None:
    ok = Tabulated(wavelength_um=(1.5, 1.6), n=(3.4, 3.5), k=(0.0, 0.0))
    assert ok.span.maximum == Quantity(value=1.6, unit="um")
    for kwargs in (
        {"wavelength_um": (1.6, 1.5), "n": (3.4, 3.5), "k": (0.0, 0.0)},
        {"wavelength_um": (1.5, 1.6), "n": (3.4,), "k": (0.0, 0.0)},
        {"wavelength_um": (1.5, 1.6), "n": (3.4, -1.0), "k": (0.0, 0.0)},
        {"wavelength_um": (1.5, 1.6), "n": (3.4, 3.5), "k": (0.0, -1.0)},
    ):
        with pytest.raises(ValidationError):
            Tabulated(**kwargs)


def test_only_linear_interpolation_is_declarable() -> None:
    with pytest.raises(ValidationError):
        Tabulated(interpolation="cubic", wavelength_um=(1.5, 1.6), n=(3, 3), k=(0, 0))  # type: ignore[arg-type]


def test_wavelength_range_diagnostics() -> None:
    um = lambda v: Quantity(value=v, unit="um")  # noqa: E731
    assert WavelengthRange(minimum=um(1.5), maximum=um(1.6)).diagnostics("s", ()) == []
    assert [
        d.code for d in WavelengthRange(minimum=um(1.6), maximum=um(1.5)).diagnostics("s", ())
    ] == ["technology.wavelength_range_invalid"]
    bad_unit = WavelengthRange(minimum=Quantity(value=1, unit="ps"), maximum=um(1.6))
    assert [d.code for d in bad_unit.diagnostics("s", ("band",))] == ["units.dimension_mismatch"]
    assert bad_unit.diagnostics("s", ("band",))[0].location == ("band", "minimum")


def test_range_containment_is_unit_aware() -> None:
    wide = WavelengthRange(
        minimum=Quantity(value=1200, unit="nm"), maximum=Quantity(value=1.7, unit="um")
    )
    assert wide.contains(Quantity(value=1.55, unit="um"))
    assert not wide.contains(Quantity(value=1.8, unit="um"))
    assert wide.covers(synthetic_soi().optical_band)


def test_manifest_rejects_bad_versions_specifiers_pins_and_ref_kinds() -> None:
    payload = synthetic_soi_pdk().manifest.model_dump(mode="json")
    for change in (
        {"version": "1.0-beta"},
        {"waffle": "~=0.1"},
        {"dependencies": [{"name": "numpy", "version": ">=2"}]},
        {"technologies": ["component:x@sha256:" + "a" * 64]},
        {"access": "secret"},
    ):
        assert not load_record({**payload, **change}, PdkManifest).ok, change


def test_pdk_needs_at_least_one_technology() -> None:
    with pytest.raises(ValidationError):
        Pdk(manifest=synthetic_soi_pdk().manifest, technologies=())


def test_manifest_pins_technology_refs() -> None:
    manifest = synthetic_soi_pdk().manifest
    assert manifest.technologies == (revision_of(synthetic_soi()),)
    assert isinstance(manifest.technologies[0], RevisionRef)
