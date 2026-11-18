from __future__ import annotations

from waffle.contracts.technology.revision import LayerPurpose, LayoutLayer, Patterned
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.technology.mapping import map_gds, map_layer


def test_layer_maps_to_every_level_it_patterns_bottom_to_top() -> None:
    mapping = map_layer(synthetic_soi(), "WG").unwrap()
    assert mapping.layer.name == "WG"
    assert [lv.name for lv in mapping.levels] == ["slab", "core"]
    assert [lv.name for lv in map_layer(synthetic_soi(), "SLAB").unwrap().levels] == ["slab"]


def test_gds_pair_maps_through_the_layer_table() -> None:
    mapping = map_gds(synthetic_soi(), 1, 0).unwrap()
    assert mapping.layer.name == "WG"
    assert [lv.name for lv in mapping.levels] == ["slab", "core"]
    # Same GDS layer number, different datatype: a different logical layer.
    assert map_gds(synthetic_soi(), 1, 10).unwrap().layer.name == "PORT"


def test_annotation_layers_map_to_no_levels_without_error() -> None:
    result = map_layer(synthetic_soi(), "PORT")
    assert result.ok and result.unwrap().levels == ()


def test_unknown_layer_and_gds_pair_are_missing_mappings() -> None:
    by_name = map_layer(synthetic_soi(), "NOPE")
    assert by_name.value is None
    assert [d.code for d in by_name.diagnostics] == ["technology.layer_unknown"]
    assert by_name.diagnostics[0].subject == "technology:synthetic_soi"
    by_gds = map_gds(synthetic_soi(), 99, 0)
    assert [d.code for d in by_gds.diagnostics] == ["technology.layer_unknown"]
    assert "99/0" in by_gds.diagnostics[0].message


def test_drawing_layer_without_a_level_is_unmapped() -> None:
    tech = synthetic_soi()
    heater = LayoutLayer(name="HEATER", gds_layer=10, gds_datatype=0)
    with_heater = tech.model_copy(update={"layers": (*tech.layers, heater)})
    assert [d.code for d in map_layer(with_heater, "HEATER").diagnostics] == [
        "technology.layer_unmapped"
    ]
    assert [d.code for d in map_gds(with_heater, 10, 0).diagnostics] == [
        "technology.layer_unmapped"
    ]


def test_duplicate_definitions_are_ambiguous_never_a_silent_pick() -> None:
    tech = synthetic_soi()
    same_pair = LayoutLayer(name="WG2", gds_layer=1, gds_datatype=0)
    same_name = LayoutLayer(name="SLAB", gds_layer=50, gds_datatype=0, purpose=LayerPurpose.DRAWING)
    broken = tech.model_copy(update={"layers": (*tech.layers, same_pair, same_name)})
    by_gds = map_gds(broken, 1, 0)
    assert by_gds.value is None
    assert [d.code for d in by_gds.diagnostics] == ["technology.layer_ambiguous"]
    assert "'WG'" in by_gds.diagnostics[0].message and "'WG2'" in by_gds.diagnostics[0].message
    assert [d.code for d in map_layer(broken, "SLAB").diagnostics] == ["technology.layer_ambiguous"]


def test_levels_returned_are_the_records_of_the_stack() -> None:
    tech = synthetic_soi()
    mapping = map_layer(tech, "WG").unwrap()
    assert all(isinstance(lv.pattern, Patterned) for lv in mapping.levels)
    assert mapping.levels[1] is tech.level("core")
