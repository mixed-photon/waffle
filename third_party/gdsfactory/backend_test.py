"""Acceptance tests of the gdsfactory backend (MOD-005), against the real library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import klayout.db as kdb
import pytest

from waffle.contracts.component.definition import PhysicalImplementation
from waffle.contracts.component.design import ResolvedDesign
from waffle.contracts.component.examples import phase_shifter
from waffle.contracts.component.parameters import DesignValues
from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.geometry.grid import Grid
from waffle.contracts.geometry.transform import Transform
from waffle.contracts.layout.examples import nested_layout
from waffle.contracts.layout.layout import Layout
from waffle.geometry.services.shapes import to_polygon
from waffle.layout.backend.conformance import Case, applicable_cases
from waffle.layout.backend.interface import (
    BackendCode,
    Capability,
    ImportRequest,
    request_for_design,
)
from waffle.layout.backend.packaging import export_package
from waffle.layout.formats.sidecar import read_package, sidecar_path, verify_sidecar, write_package
from waffle.layout.services.compare import CompareMode, compare_layouts
from waffle.layout.services.flatten import flatten
from waffle.layout.services.validate import validate_layout
from third_party.gdsfactory.backend import ADAPTER_VERSION, NAME, GdsfactoryBackend
from third_party.gdsfactory.fixtures import (
    GRID,
    TABLE,
    TECHNOLOGY,
    composite,
    generate,
    request,
    um,
)

BACKEND = GdsfactoryBackend()


def points(layout: Layout, cell: str, layer: str = "WG") -> list[tuple[tuple[int, int], ...]]:
    found = next(c for c in layout.cells if c.name == cell)
    return [to_polygon(s).unwrap().points for s in found.shapes[layer]]


def ports(layout: Layout, cell: str) -> dict[str, tuple[tuple[int, int], int, int]]:
    found = next(c for c in layout.cells if c.name == cell)
    return {p.name: (p.center, p.orientation, p.width) for p in found.ports}


def gds_layers(data: bytes) -> tuple[kdb.Layout, set[tuple[int, int]]]:
    """Read GDS bytes back with klayout directly, independently of the adapter."""
    ly = kdb.Layout()
    ly.read_bytes(data)
    return ly, {
        (i.layer, i.datatype)
        for i in ly.layer_infos()
        if not ly.top_cell().bbox_per_layer(ly.layer(i)).empty()
    }


# Shared conformance cases (the same ones the fake backend runs in the fast suite)


@pytest.mark.parametrize("case", applicable_cases(BACKEND), ids=lambda c: c.name)
def test_conformance(case: Case) -> None:
    case.run(BACKEND, TECHNOLOGY)


def test_all_four_capabilities_are_declared() -> None:
    info = BACKEND.info()
    assert info.capabilities == set(Capability)
    assert info.producer.name == NAME and info.producer.version == ADAPTER_VERSION
    assert info.producer.upstream == {
        "gdsfactory": "9.51.0",
        "kfactory": "3.0.4",
        "klayout": "0.30.13",
    }
    assert [(g.name, g.version) for g in info.generators] == [
        ("waffle.gdsfactory.straight", "1.0.0"),
        ("waffle.gdsfactory.mmi1x2", "1.0.0"),
        ("waffle.gdsfactory.mmi2x2", "1.0.0"),
    ]
    assert info.import_formats == info.export_formats == ("gds",)


# Acceptance 1: reference waveguide and MMI


def test_generates_reference_waveguide() -> None:
    layout = generate("straight", length=10, width=0.5)
    assert validate_layout(layout) == []
    assert layout.grid == GRID == Grid(dbu_pm=1000, manufacturing=5)
    assert layout.top == "straight" and [c.name for c in layout.cells] == ["straight"]
    assert set(layout.layers) == set(TABLE) and len(layout.layers) == 4  # the whole table
    assert points(layout, "straight") == [((0, -250), (10000, -250), (10000, 250), (0, 250))]
    assert ports(layout, "straight") == {"o1": ((0, 0), 180, 500), "o2": ((10000, 0), 0, 500)}
    box = flatten(layout).unwrap().bounding_box()
    assert (box.lo, box.hi) == ((0, -250), (10000, 250))
    assert layout.metadata["generator"] == "waffle.gdsfactory.straight"
    assert layout.metadata["technology"] == str(revision_of(TECHNOLOGY))
    assert all(p.layer == "WG" and p.domain == "optical" for p in layout.cells[0].ports)


def test_generation_converts_units_exactly() -> None:
    reference = generate("straight", length=10, width=0.5)
    in_other_units = request("straight").model_copy(
        update={
            "values": DesignValues(
                values={
                    "length": Quantity(value=0.01, unit="mm"),
                    "width": Quantity(value=500, unit="nm"),
                }
            )
        }
    )
    assert BACKEND.generate(in_other_units).unwrap() == reference


def test_generation_follows_the_technology_grid_and_layers() -> None:
    wg = TECHNOLOGY.layer("WG")
    assert wg is not None
    fine = TECHNOLOGY.model_copy(
        update={
            "dbu_pm": 250,
            "manufacturing_grid_pm": 250,
            "layers": tuple(
                layer.model_copy(update={"gds_layer": 7, "gds_datatype": 3})
                if layer is wg
                else layer
                for layer in TECHNOLOGY.layers
            ),
        }
    )
    layout = BACKEND.generate(request("straight", technology=fine, length=10, width=0.5)).unwrap()
    assert layout.grid == Grid(dbu_pm=250, manufacturing=1)
    assert points(layout, "straight") == [((0, -1000), (40000, -1000), (40000, 1000), (0, 1000))]
    data = BACKEND.export_gds(layout).unwrap()
    ly, used = gds_layers(data)
    assert used == {(7, 3)}  # from the technology layer table, not gdsfactory's generic (1, 0)
    assert ly.dbu == pytest.approx(0.00025)
    assert ly.top_cell().bbox() == kdb.Box(0, -1000, 40000, 1000)


def test_generates_reference_mmi1x2() -> None:
    layout = generate("mmi1x2")
    assert validate_layout(layout) == []
    assert layout.top == "mmi1x2"
    assert [c.name for c in layout.cells] == ["mmi1x2", "mmi1x2_taper"]
    assert ports(layout, "mmi1x2") == {
        "o1": ((-10000, 0), 180, 500),
        "o2": ((15500, 625), 0, 500),
        "o3": ((15500, -625), 0, 500),
    }
    assert points(layout, "mmi1x2") == [((0, -1250), (5500, -1250), (5500, 1250), (0, 1250))]
    assert points(layout, "mmi1x2_taper") == [((0, -250), (10000, -500), (10000, 500), (0, 250))]
    assert ports(layout, "mmi1x2_taper") == {"o1": ((0, 0), 180, 500), "o2": ((10000, 0), 0, 1000)}
    top = next(c for c in layout.cells if c.name == "mmi1x2")
    assert {i.name: (i.cell, i.transform) for i in top.instances} == {
        "in1": ("mmi1x2_taper", Transform(translation=(-10000, 0))),
        "out1": ("mmi1x2_taper", Transform(rotation=180, translation=(15500, 625))),
        "out2": ("mmi1x2_taper", Transform(rotation=180, translation=(15500, -625))),
    }
    flat = flatten(layout).unwrap()
    box = flat.bounding_box()
    assert (box.lo, box.hi) == ((-10000, -1250), (15500, 1250))
    assert len(flat.polygons["WG"]) == 4  # body and three tapers
    (_, ((x2, y2), _, _)), (_, ((x3, y3), _, _)) = (
        (name, v) for name, v in ports(layout, "mmi1x2").items() if name in ("o2", "o3")
    )
    assert (x2, y2) == (x3, -y3)  # outputs are symmetric about the axis


def test_generates_reference_mmi2x2() -> None:
    layout = generate("mmi2x2", length_mmi=6, gap_mmi=0.3)
    assert validate_layout(layout) == []
    offset = (300 + 1000) // 2
    assert ports(layout, "mmi2x2") == {
        "o1": ((-10000, -offset), 180, 500),
        "o2": ((-10000, offset), 180, 500),
        "o3": ((16000, offset), 0, 500),
        "o4": ((16000, -offset), 0, 500),
    }
    assert len(flatten(layout).unwrap().polygons["WG"]) == 5


def test_generation_is_deterministic_and_named_by_request() -> None:
    first, second = generate("mmi1x2"), generate("mmi1x2")
    assert first == second and revision_of(first) == revision_of(second)
    assert first.id != generate("mmi1x2", length_mmi=6).id
    named = request("straight", length=5).model_copy(
        update={"layout_id": EntityId("layout", "wg5")}
    )
    assert BACKEND.generate(named).unwrap().id == EntityId("layout", "wg5")


def test_generation_diagnostics_name_the_problem() -> None:
    def codes(name: str, **lengths: float) -> list[str]:
        return [d.code for d in BACKEND.generate(request(name, **lengths)).diagnostics]

    assert codes("straight") == ["params.missing"]
    assert codes("straight", length=10, depth=1) == ["params.unknown"]
    assert codes("straight", length=0.0015) == ["geometry.grid.off_grid"]
    assert codes("straight", length=10, width=0.499) == [BackendCode.REQUEST_INVALID]
    assert set(codes("straight", length=10.001)) == {"layout.grid.off_manufacturing"}
    assert set(codes("mmi1x2", width_taper=0.999)) == {BackendCode.REQUEST_INVALID}
    assert codes("mmi1x2", width_mmi=1.5) == [BackendCode.REQUEST_INVALID]
    assert codes("mmi1x2", length_mmi=0) == ["params.out_of_bounds"]
    no_wg = TECHNOLOGY.model_copy(
        update={"layers": tuple(layer for layer in TECHNOLOGY.layers if layer.name != "WG")}
    )
    result = BACKEND.generate(request("straight", technology=no_wg, length=10))
    assert [d.code for d in result.diagnostics] == [BackendCode.LAYER_MISSING]
    odd_grid = TECHNOLOGY.model_copy(update={"manufacturing_grid_pm": 2500})
    result = BACKEND.generate(request("straight", technology=odd_grid, length=10))
    assert [d.code for d in result.diagnostics] == [BackendCode.GRID_MISMATCH]


# Acceptance 2: hierarchy, layer mappings, ports, and transforms survive


def test_preserves_hierarchy_layers_ports_and_transforms() -> None:
    layout = nested_layout()
    package = export_package(BACKEND, layout).unwrap()
    imported = BACKEND.import_gds(package.gds, ImportRequest(sidecar=package.sidecar)).unwrap()
    assert compare_layouts(layout, imported) == []
    # hierarchy and exact transforms
    top = next(c for c in imported.cells if c.name == "top")
    assert {i.name: (i.cell, i.transform) for i in top.instances} == {
        "m1": ("mid", Transform(rotation=90, translation=(100, 0))),
        "m2": ("mid", Transform(mirror=True, rotation=180, translation=(0, 50))),
    }
    mid = next(c for c in imported.cells if c.name == "mid")
    assert [(i.name, i.cell, i.transform) for i in mid.instances] == [
        ("l1", "leaf", Transform(mirror=True, translation=(10, 0)))
    ]
    # layer mapping: logical names back, GDS pairs as in the table, read independently
    assert imported.layers == layout.layers
    ly, used = gds_layers(package.gds)
    assert used == {(1, 0), (41, 0)}
    assert {c.name for c in ly.each_cell()} == {"top", "mid", "leaf"}
    # ports, with their orientation through mirrored and rotated instances
    assert ports(imported, "leaf") == ports(layout, "leaf")
    flat = {q.path: (q.port.center, q.port.orientation) for q in flatten(imported).unwrap().ports}
    assert flat["m1/l1/o1"] == ((101, 10), 270)  # mirror, rotate by 90, move
    assert flat["m2/l1/o2"] == ((-12, 50), 90)  # two mirrors and a half turn
    assert flat["in"] == ((0, 0), 180)


def test_composite_with_mirrored_instance_preserves_everything() -> None:
    layout = composite()
    assert validate_layout(layout) == []
    flat = flatten(layout).unwrap()
    found = {q.path: (q.port.center, q.port.orientation) for q in flat.ports}
    # the mirrored MMI faces back, and every waveguide end mates a port (opposite orientation)
    assert found["mmi_b/o1"] == ((61000, 0), 0)
    for arm, mmi_a, mmi_b in (("arm_upper", "o2", "o2"), ("arm_lower", "o3", "o3")):
        assert found[f"{arm}/o1"][0] == found[f"mmi_a/{mmi_a}"][0]
        assert found[f"{arm}/o2"][0] == found[f"mmi_b/{mmi_b}"][0]
        assert (found[f"{arm}/o1"][1] - found[f"mmi_a/{mmi_a}"][1]) % 360 == 180
        assert (found[f"{arm}/o2"][1] - found[f"mmi_b/{mmi_b}"][1]) % 360 == 180
    package = export_package(BACKEND, layout).unwrap()
    imported = BACKEND.import_gds(package.gds, ImportRequest(sidecar=package.sidecar)).unwrap()
    for mode in CompareMode:
        assert compare_layouts(layout, imported, mode) == []
    mzi = next(c for c in imported.cells if c.name == "mzi")
    assert {i.name: i.transform for i in mzi.instances}["mmi_b"] == Transform(
        mirror=True, rotation=180, translation=(51000, 0)
    )
    assert {i.cell for i in mzi.instances} == {"mmi1x2", "straight"}


# Acceptance 3: export and re-import for semantic comparison


@pytest.mark.parametrize(
    "layout",
    [
        generate("straight", length=10),
        generate("mmi1x2"),
        generate("mmi2x2"),
        composite(),
        nested_layout(),
    ],
    ids=lambda layout: layout.top,
)
def test_export_and_reimport_compare_equal(layout: Layout) -> None:
    package = export_package(BACKEND, layout).unwrap()
    imported = BACKEND.import_gds(package.gds, ImportRequest(sidecar=package.sidecar)).unwrap()
    assert imported.id == layout.id and imported.grid == layout.grid
    assert compare_layouts(layout, imported, CompareMode.HIERARCHICAL) == []
    assert compare_layouts(layout, imported, CompareMode.FLAT) == []
    again = BACKEND.export_gds(imported).unwrap()
    assert again == package.gds


def test_exported_gds_geometry_read_back_independently() -> None:
    data = export_package(BACKEND, generate("mmi1x2")).unwrap().gds
    ly, used = gds_layers(data)
    assert ly.dbu == 0.001 and used == {(1, 0)}
    top = ly.top_cell()
    assert top.name == "mmi1x2"
    assert top.bbox() == kdb.Box(-10000, -1250, 15500, 1250)
    assert sorted(c.name for c in ly.each_cell()) == ["mmi1x2", "mmi1x2_taper"]
    assert sorted(i.property(0) for i in top.each_inst()) == ["in1", "out1", "out2"]
    merged = kdb.Region(top.begin_shapes_rec(ly.layer(1, 0))).merged()
    assert merged.area() == 5500 * 2500 + 3 * (500 + 1000) * 10000 // 2  # body and three tapers


def test_import_without_a_sidecar_maps_layers_and_has_no_ports() -> None:
    layout = generate("mmi1x2")
    data = BACKEND.export_gds(layout).unwrap()
    result = BACKEND.import_gds(
        data, ImportRequest(layers=layout.layers, layout_id=EntityId("layout", "bare"))
    )
    bare = result.unwrap()
    assert [d.code for d in result.diagnostics] == [BackendCode.NO_PORTS]
    assert result.diagnostics[0].severity == "info"
    assert all(not c.ports for c in bare.cells)
    assert points(bare, "mmi1x2") == points(layout, "mmi1x2")
    unmapped = ImportRequest(
        layers=tuple(s for s in layout.layers if s.name != "WG"),
        layout_id=EntityId("layout", "bare"),
    )
    lost = BACKEND.import_gds(data, unmapped)
    errors = [d for d in lost.diagnostics if d.severity == "error"]
    assert lost.value is None and {d.code for d in errors} == {BackendCode.LAYER_UNMAPPED}
    assert {d.location for d in errors} == {
        ("cells", "mmi1x2", "shapes", "1/0"),
        ("cells", "mmi1x2_taper", "shapes", "1/0"),
    }


# Acceptance 4: sidecar association with the exported GDS


def test_sidecar_is_associated_with_the_exported_gds(tmp_path: Path) -> None:
    layout = generate("mmi1x2")
    example = phase_shifter()
    design = example.design.model_copy(update={"technology": revision_of(TECHNOLOGY)})
    package = export_package(
        BACKEND,
        layout,
        design=revision_of(design),
        technology=revision_of(TECHNOLOGY),
        implementation=revision_of(example.implementation),
    ).unwrap()
    gds_path = tmp_path / "mmi.gds"
    sidecar_file = write_package(gds_path, package)
    assert sidecar_file == sidecar_path(gds_path) == tmp_path / "mmi.gds.waffle.json"
    on_disk = json.loads(sidecar_file.read_text())
    assert on_disk["sha256"] == "sha256:" + hashlib.sha256(gds_path.read_bytes()).hexdigest()
    assert on_disk["size"] == gds_path.stat().st_size
    assert on_disk["top"] == "mmi1x2" and on_disk["grid"] == {"dbu_pm": 1000, "manufacturing": 5}
    assert on_disk["layout"] == str(revision_of(layout))
    assert on_disk["technology"] == str(revision_of(TECHNOLOGY))
    assert on_disk["producer"] == {
        "name": NAME,
        "version": ADAPTER_VERSION,
        "upstream": {"gdsfactory": "9.51.0", "kfactory": "3.0.4", "klayout": "0.30.13"},
    }
    top_ports = next(item for item in on_disk["ports"] if item["cell"] == "mmi1x2")["ports"]
    assert [(p["name"], p["center"], p["orientation"]) for p in top_ports] == [
        ("o1", [-10000, 0], 180),
        ("o2", [15500, 625], 0),
        ("o3", [15500, -625], 0),
    ]
    assert {item["cell"] for item in on_disk["ports"]} == {"mmi1x2", "mmi1x2_taper"}
    assert read_package(gds_path).unwrap() == package


def test_modified_gds_wrong_sidecar_and_wrong_top_are_detected(tmp_path: Path) -> None:
    package = export_package(BACKEND, generate("mmi1x2")).unwrap()
    flipped = bytearray(package.gds)
    flipped[-20] ^= 0xFF
    assert [d.code for d in verify_sidecar(bytes(flipped), package.sidecar)] == [
        "gds.sidecar.hash_mismatch"
    ]
    other = export_package(BACKEND, generate("mmi2x2")).unwrap()
    assert [d.code for d in verify_sidecar(other.gds, package.sidecar)] == [
        "gds.sidecar.size_mismatch"
    ]
    wrong_top = package.sidecar.model_copy(update={"top": "mmi1x2_taper"})
    assert [d.code for d in verify_sidecar(package.gds, wrong_top)] == ["gds.sidecar.top_mismatch"]
    refused = BACKEND.import_gds(bytes(flipped), ImportRequest(sidecar=package.sidecar))
    assert refused.value is None and "gds.sidecar.hash_mismatch" in [
        d.code for d in refused.diagnostics
    ]
    gds_path = tmp_path / "mmi.gds"
    write_package(gds_path, package)
    gds_path.write_bytes(bytes(flipped))
    assert [d.code for d in read_package(gds_path).diagnostics] == ["gds.sidecar.hash_mismatch"]


def test_import_uses_the_sidecar_top_cell_and_layer_table() -> None:
    package = export_package(BACKEND, composite()).unwrap()
    imported = BACKEND.import_gds(package.gds, ImportRequest(sidecar=package.sidecar)).unwrap()
    assert imported.top == "mzi" and imported.layers == package.sidecar.layers


def mmi_design() -> tuple[ResolvedDesign, PhysicalImplementation]:
    """A resolved design of the 1x2 MMI generator, pinned to the synthetic technology."""
    example = phase_shifter()
    implementation = example.implementation.model_copy(
        update={"source": request("mmi1x2").generator}
    )
    design = example.design.model_copy(
        update={
            "implementation": revision_of(implementation),
            "parameters": DesignValues(values={"length_mmi": um(6)}),
            "technology": revision_of(TECHNOLOGY),
        }
    )
    return design, implementation


def test_design_to_exported_gds_with_traceable_sidecar() -> None:
    design, implementation = mmi_design()
    generation = request_for_design(design, implementation, TECHNOLOGY).unwrap()
    layout = BACKEND.generate(generation).unwrap()
    assert layout.id == EntityId("layout", design.id.name)
    assert ports(layout, "mmi1x2")["o2"] == ((16000, 625), 0, 500)
    package = export_package(
        BACKEND,
        layout,
        design=revision_of(design),
        technology=design.technology,
        implementation=design.implementation,
    ).unwrap()
    sidecar = package.sidecar
    assert (sidecar.layout, sidecar.design) == (revision_of(layout), revision_of(design))
    assert (sidecar.technology, sidecar.implementation) == (
        revision_of(TECHNOLOGY),
        revision_of(implementation),
    )
    assert layout.metadata["technology"] == str(sidecar.technology)
