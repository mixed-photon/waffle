from __future__ import annotations

from waffle.contracts.foundation.identity import EntityId, RevisionRef
from waffle.contracts.foundation.records import (
    load_record,
    revision_of,
    to_canonical_json,
)
from waffle.contracts.mappings.mapping import ChannelBinding, PortAnchor, ViewMapping


def test_mapping_round_trip_pins_all_sources() -> None:
    ref = lambda kind: RevisionRef(EntityId(kind, "x"), "sha256:" + "a" * 64)
    mapping = ViewMapping(
        id=EntityId("view_mapping", "x"),
        interface=ref("interface"),
        layout=ref("layout"),
        problem=ref("physical_problem"),
        bindings=(
            ChannelBinding(
                terminal="a",
                layout_port="i/p",
                anchor=PortAnchor(center=(1, 2), orientation=90),
                simulation_port="i/p",
                model_channel="c0",
            ),
        ),
    )
    restored = load_record(to_canonical_json(mapping), ViewMapping).unwrap()
    assert restored == mapping
    assert revision_of(restored) == revision_of(mapping)
