"""Isolation from gdsfactory's global state, reproducibility, and the declared runtime."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gdsfactory as gf
import gdsfactory.pdk as gf_pdk
import kfactory as kf
import klayout.db as kdb
import pytest

from waffle.contracts.component.definition import GeneratorSource
from waffle.layout.backend.interface import GenerationRequest, ImportRequest
from waffle.layout.services.flatten import flatten
from third_party.gdsfactory.backend import GdsfactoryBackend
from third_party.gdsfactory.fixtures import TECHNOLOGY, composite, generate, request, run_python
from third_party.gdsfactory.generators import GENERATORS
from third_party.gdsfactory.session import session

BACKEND = GdsfactoryBackend()


def global_state() -> tuple[object, ...]:
    return (
        gf_pdk._ACTIVE_PDK,
        sorted(kf.layout.kcls),
        len(kf.kcl.kcells),
        kf.kcl.dbu,
        [str(info) for info in kf.kcl.layout.layer_infos()],
        sorted(kdb.Library.library_names()),
    )


def test_generation_import_and_export_leave_no_global_state() -> None:
    before = global_state()
    for generator in GENERATORS:
        source = GeneratorSource(name=generator.info.name, version=generator.info.version)
        example = GenerationRequest(
            generator=source, values=generator.info.example, technology=TECHNOLOGY
        )
        layout = BACKEND.generate(example).unwrap()
        data = BACKEND.export_gds(layout).unwrap()
        BACKEND.import_gds(data, ImportRequest(layers=layout.layers, layout_id=layout.id)).unwrap()
    composite()
    assert BACKEND.generate(request("straight")).value is None  # a failing request too
    assert global_state() == before


def test_concurrent_generation_is_serialized_and_deterministic() -> None:
    before = global_state()
    with ThreadPoolExecutor(max_workers=4) as pool:
        layouts = list(pool.map(lambda _: generate("mmi2x2"), range(12)))
    assert all(layout == layouts[0] for layout in layouts)
    assert global_state() == before


def test_a_failure_inside_a_session_still_restores_everything() -> None:
    before = global_state()
    with pytest.raises(RuntimeError, match="boom"), session(250) as kcl:
        assert kcl.dbu == 0.00025
        raise RuntimeError("boom")
    assert global_state() == before


def test_a_pdk_the_caller_activated_is_neither_used_nor_changed() -> None:
    sentinel = gf.Pdk(name="caller_pdk")
    previous = gf_pdk._ACTIVE_PDK
    gf_pdk._ACTIVE_PDK = sentinel
    try:
        layout = generate("straight", length=10)
        assert gf_pdk._ACTIVE_PDK is sentinel
        assert layout.layers and layout.cells[0].ports
    finally:
        gf_pdk._ACTIVE_PDK = previous


def test_generation_next_to_the_generic_pdk_uses_the_technology_not_the_generic_layers() -> None:
    """In a fresh interpreter: activate gdsfactory's generic PDK (which rewrites the default
    layout), then generate; the generic PDK, its layers, and its cells must be untouched."""
    code = """
import json, gdsfactory as gf, kfactory as kf
from third_party.gdsfactory.fixtures import generate
gf.gpdk.PDK.activate()
gf.components.straight(length=7)  # a cell in the default layout, as a user would build
def state():
    layers = len(kf.kcl.layout.layer_infos())
    return [gf.get_active_pdk().name, len(kf.kcl.kcells), kf.kcl.dbu, layers]
before = state()
layout = generate("mmi1x2")
after = state()
print(json.dumps([before, after, sorted(kf.layout.kcls)]))
"""
    before, after, registry = json.loads(run_python(code))
    assert before == after and before[0] == "generic"
    assert registry == ["DEFAULT"]


def test_gds_bytes_are_reproducible_across_processes() -> None:
    here = hashlib.sha256(BACKEND.export_gds(generate("mmi2x2")).unwrap()).hexdigest()
    code = """
import hashlib
from third_party.gdsfactory.backend import GdsfactoryBackend
from third_party.gdsfactory.fixtures import generate
print(hashlib.sha256(GdsfactoryBackend().export_gds(generate("mmi2x2")).unwrap()).hexdigest())
"""
    assert run_python(code).strip() == here


UPSTREAM_PORTS = """
import json, gdsfactory as gf
gf.gpdk.PDK.activate()
c = {call}
print(json.dumps({{
    "ports": {{p.name: [p.center[0], p.center[1], p.orientation, p.width] for p in c.ports}},
    "bbox": [c.xmin, c.ymin, c.xmax, c.ymax],
}}))
"""


@pytest.mark.parametrize(
    ("name", "lengths", "call"),
    [
        ("straight", {"length": 10}, "gf.components.straight(length=10)"),
        ("mmi1x2", {}, "gf.components.mmi1x2()"),
        (
            "mmi2x2",
            {"length_mmi": 6, "gap_mmi": 0.3},
            "gf.components.mmi2x2(length_mmi=6, gap_mmi=0.3)",
        ),
    ],
)
def test_generated_ports_and_extent_match_gdsfactorys_own_components(
    name: str, lengths: dict[str, float], call: str
) -> None:
    upstream = json.loads(run_python(UPSTREAM_PORTS.format(call=call)))
    layout = generate(name, **lengths)
    top = next(c for c in layout.cells if c.name == layout.top)
    ours = {
        p.name: [p.center[0] / 1000, p.center[1] / 1000, p.orientation, p.width / 1000]
        for p in top.ports
    }
    assert ours.keys() == upstream["ports"].keys()
    for port, values in ours.items():
        assert values == pytest.approx(upstream["ports"][port]), port
    box = flatten(layout).unwrap().bounding_box()
    assert [
        box.lo[0] / 1000,
        box.lo[1] / 1000,
        box.hi[0] / 1000,
        box.hi[1] / 1000,
    ] == pytest.approx(upstream["bbox"])


# The declared runtime: the pinned @pip_gdsfactory hub, next to @pip_core


def pinned_version() -> str:
    for line in Path(os.environ["REQUIREMENTS_IN"]).read_text().splitlines():
        name, sep, version = line.partition("==")
        if sep and name.strip() == "gdsfactory":
            return version.strip()
    raise AssertionError("gdsfactory is not pinned in requirements.in")


def distributions(name: str) -> list[tuple[str, str]]:
    """Every copy of distribution `name` on `sys.path` as (location, version)."""
    found = []
    for entry in sys.path:
        for dist in importlib.metadata.distributions(path=[entry]):
            if (dist.metadata["Name"] or "").lower().replace("_", "-") == name:
                found.append((entry, dist.version))
    return found


def test_runs_against_the_pinned_upstream_from_its_own_hub() -> None:
    assert gf.__version__ == pinned_version() == "9.51.0"
    assert "pip_gdsfactory" in gf.__file__
    info = BACKEND.info()
    assert info.producer.upstream == {
        "gdsfactory": gf.__version__,
        "kfactory": kf.__version__,
        "klayout": kdb.__version__,
    }
    assert "pip_gdsfactory" in kf.__file__ and "pip_gdsfactory" in kdb.__file__


@pytest.mark.parametrize(
    "package", ["pydantic", "pydantic-core", "numpy", "typing-extensions", "pytest"]
)
def test_shared_packages_have_one_version_on_the_path(package: str) -> None:
    """Core and backend hubs are separate; where both put a package on the path the versions
    must be identical (the first one on the path is the one imported)."""
    versions = {version for _, version in distributions(package)}
    assert len(versions) == 1, distributions(package)


def test_core_backend_interface_does_not_import_the_upstream_library() -> None:
    code = """
import importlib.util, sys
assert importlib.util.find_spec("gdsfactory") is not None  # importable, so absence means something
import waffle.layout.backend.interface, waffle.layout.backend.packaging
import waffle.layout.backend.fake, waffle.layout.backend.conformance
import waffle.layout.formats.sidecar, waffle.layout.formats.publish, waffle.layout.formats.gds_stream
loaded = {m.split(".")[0] for m in sys.modules} & {"gdsfactory", "kfactory", "klayout"}
print(sorted(loaded))
"""
    assert run_python(code).strip() == "[]"
