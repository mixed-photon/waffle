from __future__ import annotations

import hashlib
from pathlib import Path

from waffle.contracts.component.examples import TECHNOLOGY, phase_shifter
from waffle.contracts.foundation.diagnostics import Diagnostic
from waffle.contracts.foundation.records import load_record, revision_of, to_canonical_json
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.shapes import Polygon
from waffle.contracts.layout.examples import nested_layout
from waffle.layout.formats.gds_stream import summarize
from waffle.layout.formats.sidecar import (
    GdsPackage,
    GdsSidecar,
    Producer,
    build_sidecar,
    read_package,
    sidecar_path,
    verify_sidecar,
    write_package,
)
from waffle.layout.formats.testing import gds_bytes

PRODUCER = Producer(name="fake", version="1.0.0", upstream={"gdsfactory": "9.51.0"})
LAYOUT = nested_layout()
GDS = gds_bytes(LAYOUT)


def package() -> GdsPackage:
    return GdsPackage(GDS, build_sidecar(LAYOUT, GDS, PRODUCER))


def codes(diagnostics: list[Diagnostic]) -> list[str]:
    return [d.code for d in diagnostics]


def test_sidecar_describes_the_exported_bytes() -> None:
    sidecar = package().sidecar
    assert sidecar.sha256 == "sha256:" + hashlib.sha256(GDS).hexdigest()
    assert sidecar.size == len(GDS)
    assert sidecar.layout == revision_of(LAYOUT)
    assert (sidecar.top, sidecar.grid, sidecar.layers) == ("top", Grid(dbu_pm=1000), LAYOUT.layers)
    assert [item.cell for item in sidecar.ports] == ["leaf", "mid", "top"]
    assert [p.name for p in sidecar.ports_of("leaf")] == ["o1", "o2"]
    assert sidecar.ports_of("missing") == ()
    assert sidecar.producer == PRODUCER
    assert verify_sidecar(GDS, sidecar) == []


def test_sidecar_round_trips_through_canonical_json() -> None:
    sidecar = package().sidecar
    loaded = load_record(to_canonical_json(sidecar), GdsSidecar).unwrap()
    assert loaded == sidecar and revision_of(loaded) == revision_of(sidecar)


def test_sidecar_records_traceability_refs() -> None:
    example = phase_shifter()
    sidecar = build_sidecar(
        LAYOUT,
        GDS,
        PRODUCER,
        design=example.design_ref,
        technology=TECHNOLOGY,
        implementation=revision_of(example.implementation),
        metadata={"note": "demo"},
    )
    loaded = load_record(to_canonical_json(sidecar), GdsSidecar).unwrap()
    assert (loaded.design, loaded.technology) == (example.design_ref, TECHNOLOGY)
    assert loaded.implementation == revision_of(example.implementation)


def test_modified_gds_is_detected() -> None:
    sidecar = package().sidecar
    flipped = bytearray(GDS)
    flipped[-10] ^= 0xFF
    assert len(flipped) == len(GDS)
    assert codes(verify_sidecar(bytes(flipped), sidecar)) == ["gds.sidecar.hash_mismatch"]
    assert codes(verify_sidecar(GDS + b"\0\0", sidecar)) == ["gds.sidecar.size_mismatch"]
    assert codes(verify_sidecar(GDS[:-2], sidecar)) == ["gds.sidecar.size_mismatch"]


def test_sidecar_of_another_file_is_detected() -> None:
    leaf = next(c for c in LAYOUT.cells if c.name == "leaf")
    moved = leaf.model_copy(update={"shapes": {"WG": (Polygon(points=((0, 0), (5, 0), (0, 2))),)}})
    other = LAYOUT.model_copy(
        update={"cells": (*(c for c in LAYOUT.cells if c is not leaf), moved)}
    )
    other_gds = gds_bytes(other)
    assert len(other_gds) == len(GDS) and other_gds != GDS
    assert codes(verify_sidecar(other_gds, package().sidecar)) == ["gds.sidecar.hash_mismatch"]


def test_wrong_top_cell_is_detected_even_when_the_digest_matches() -> None:
    sidecar = package().sidecar.model_copy(update={"top": "mid"})  # `mid` is referenced by `top`
    found = verify_sidecar(GDS, sidecar)
    assert codes(found) == ["gds.sidecar.top_mismatch"]
    assert "'top'" in found[0].message
    unknown = package().sidecar.model_copy(update={"top": "nowhere"})
    assert codes(verify_sidecar(GDS, unknown)) == ["gds.sidecar.top_mismatch"]


def test_structure_mismatches_are_detected() -> None:
    sidecar = package().sidecar
    coarse = sidecar.model_copy(update={"grid": Grid(dbu_pm=5000)})
    assert codes(verify_sidecar(GDS, coarse)) == ["gds.sidecar.grid_mismatch"]
    fewer = sidecar.model_copy(update={"layers": sidecar.layers[:1]})
    assert codes(verify_sidecar(GDS, fewer)) == ["gds.sidecar.layer_mismatch"]
    stray = sidecar.model_copy(
        update={"ports": (*sidecar.ports, sidecar.ports[0].model_copy(update={"cell": "ghost"}))}
    )
    assert codes(verify_sidecar(GDS, stray)) == ["gds.sidecar.cell_mismatch"]


def test_bytes_that_are_not_gds_are_invalid() -> None:
    junk = b"not a gds file at all"
    sidecar = build_sidecar(LAYOUT, junk, PRODUCER)
    assert codes(verify_sidecar(junk, sidecar)) == ["gds.stream.invalid"]


def test_package_files_sit_side_by_side(tmp_path: Path) -> None:
    gds_path = tmp_path / "nested.gds"
    written = write_package(gds_path, package())
    assert written == sidecar_path(gds_path) == tmp_path / "nested.gds.waffle.json"
    assert gds_path.read_bytes() == GDS
    loaded = read_package(gds_path).unwrap()
    assert loaded == package()
    assert summarize(loaded.gds).unwrap().top_cells == (loaded.sidecar.top,)


def test_reading_a_package_checks_association(tmp_path: Path) -> None:
    gds_path = tmp_path / "nested.gds"
    assert [d.code for d in read_package(gds_path).diagnostics] == ["gds.file.missing"]
    gds_path.write_bytes(GDS)
    assert [d.code for d in read_package(gds_path).diagnostics] == ["gds.sidecar.missing"]
    write_package(gds_path, package())
    gds_path.write_bytes(GDS + b"\0\0")
    assert [d.code for d in read_package(gds_path).diagnostics] == ["gds.sidecar.size_mismatch"]
    gds_path.write_bytes(GDS)
    sidecar_path(gds_path).write_text("{}", encoding="utf-8")
    result = read_package(gds_path)
    assert result.value is None and {d.code for d in result.diagnostics} == {"record.kind_mismatch"}
