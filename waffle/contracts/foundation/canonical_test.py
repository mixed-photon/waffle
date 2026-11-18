from __future__ import annotations

import pytest

from waffle.contracts.foundation.canonical import canonical_json, content_hash


def test_key_order_does_not_matter() -> None:
    assert canonical_json({"b": 1, "a": {"y": 2, "x": 3}}) == '{"a":{"x":3,"y":2},"b":1}'
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_tuples_and_lists_are_equivalent() -> None:
    assert canonical_json((1, [2, 3])) == canonical_json([1, (2, 3)]) == "[1,[2,3]]"


def test_int_and_float_are_distinct() -> None:
    assert canonical_json([1, 1.0]) == "[1,1.0]"
    assert content_hash(1) != content_hash(1.0)


def test_float_uses_shortest_round_trip_repr() -> None:
    assert canonical_json(0.1) == "0.1"
    assert canonical_json(1e-7) == "1e-07"
    assert float(canonical_json(1 / 3)) == 1 / 3


def test_negative_zero_is_normalized() -> None:
    assert canonical_json(-0.0) == "0.0"
    assert content_hash(-0.0) == content_hash(0.0)


def test_bool_is_not_an_int() -> None:
    assert canonical_json([True, 1]) == "[true,1]"


def test_non_ascii_is_preserved() -> None:
    assert canonical_json("µm") == '"µm"'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_floats_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match=r"\$\.x"):
        canonical_json({"x": bad})


@pytest.mark.parametrize("bad", [{1, 2}, b"raw", {1: "a"}, object()])
def test_unsupported_values_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        canonical_json([bad])


def test_error_reports_location() -> None:
    with pytest.raises(TypeError, match=r"\$\.a\[1\]"):
        canonical_json({"a": [1, {2}]})


def test_content_hash_golden_value() -> None:
    assert content_hash({"a": 1}) == (
        "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )
