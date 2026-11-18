"""SYNTHETIC technology and PDK for tests. Not foundry data; not for fabrication or design.

A generic 220 nm silicon-on-insulator strip platform with a 90 nm slab for rib waveguides.
The geometry and layer numbers are made up but plausible. Only the silica model is a
published fit; the silicon index is a flat stand-in. Tests in any package may reuse it.

Stack (z up, SI-equivalent values): silicon substrate -727 to -2 um, buried oxide -2 to 0 um,
silicon slab 0 to 90 nm (drawn on `WG` or `SLAB`), silicon core 90 to 220 nm (drawn on
`WG`), oxide cladding 220 nm to 2.22 um. Everything else is oxide.
"""

from __future__ import annotations

from waffle.contracts.foundation.identity import EntityId
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.technology.materials import (
    ConstantIndex,
    Material,
    Sellmeier,
    WavelengthRange,
)
from waffle.contracts.technology.pdk import Access, DependencyPin, Pdk, PdkManifest
from waffle.contracts.technology.revision import (
    Blanket,
    LayerPurpose,
    LayoutLayer,
    Patterned,
    ProcessLevel,
    ProcessStack,
    TechnologyRevision,
)

TECHNOLOGY_ID = EntityId("technology", "synthetic_soi")
PDK_ID = EntityId("pdk", "synthetic_soi")
SYNTHETIC_NOTICE = "SYNTHETIC: made-up values for tests; not foundry data."


def _um(value: float) -> Quantity:
    return Quantity(value=value, unit="um")


def _nm(value: float) -> Quantity:
    return Quantity(value=value, unit="nm")


def _range(low_um: float, high_um: float) -> WavelengthRange:
    return WavelengthRange(minimum=_um(low_um), maximum=_um(high_um))


def synthetic_soi() -> TechnologyRevision:
    return TechnologyRevision(
        id=TECHNOLOGY_ID,
        dbu_pm=1000,
        manufacturing_grid_pm=5000,
        optical_band=_range(1.5, 1.6),
        layers=(
            LayoutLayer(name="WG", gds_layer=1, gds_datatype=0, description="Waveguide core"),
            LayoutLayer(name="SLAB", gds_layer=2, gds_datatype=0, description="90 nm slab"),
            LayoutLayer(
                name="PORT",
                gds_layer=1,
                gds_datatype=10,
                purpose=LayerPurpose.ANNOTATION,
                description="Optical port markers",
            ),
            LayoutLayer(
                name="DEVREC",
                gds_layer=68,
                gds_datatype=0,
                purpose=LayerPurpose.ANNOTATION,
                description="Device recognition",
            ),
        ),
        materials=(
            Material(
                name="si",
                optical=ConstantIndex(n=3.476),
                validity=_range(1.2, 1.7),
                source="Synthetic flat stand-in for crystalline silicon near 1.55 um.",
            ),
            Material(
                name="sio2",
                # Malitson, J. Opt. Soc. Am. 55, 1205 (1965), fused silica; C = (lambda_i)^2.
                optical=Sellmeier(
                    b=(0.6961663, 0.4079426, 0.8974794),
                    c_um2=(0.0684043**2, 0.1162414**2, 9.896161**2),
                ),
                validity=_range(0.21, 3.71),
                source="Malitson 1965, J. Opt. Soc. Am. 55, 1205 (fused silica, 20 degC).",
            ),
        ),
        stack=ProcessStack(
            background="sio2",
            levels=(
                ProcessLevel(
                    name="substrate",
                    z_min=_um(-727),
                    thickness=_um(725),
                    material="si",
                    pattern=Blanket(),
                ),
                ProcessLevel(
                    name="box", z_min=_um(-2), thickness=_um(2), material="sio2", pattern=Blanket()
                ),
                ProcessLevel(
                    name="slab",
                    z_min=_nm(0),
                    thickness=_nm(90),
                    material="si",
                    pattern=Patterned(layers=("WG", "SLAB"), fill="sio2"),
                ),
                ProcessLevel(
                    name="core",
                    z_min=_nm(90),
                    thickness=_nm(130),
                    material="si",
                    pattern=Patterned(layers=("WG",), fill="sio2"),
                ),
                ProcessLevel(
                    name="cladding",
                    z_min=_nm(220),
                    thickness=_um(2),
                    material="sio2",
                    pattern=Blanket(),
                ),
            ),
        ),
        metadata={"notice": SYNTHETIC_NOTICE},
    )


def synthetic_soi_pdk() -> Pdk:
    technology = synthetic_soi()
    return Pdk(
        manifest=PdkManifest(
            id=PDK_ID,
            version="1.0.0",
            waffle=">=0.0.0,<1.0.0",
            dependencies=(DependencyPin(name="numpy", version="2.5.5"),),
            access=Access.PUBLIC,
            license="CC0-1.0",
            technologies=(revision_of(technology),),
            metadata={"notice": SYNTHETIC_NOTICE},
        ),
        technologies=(technology,),
    )
