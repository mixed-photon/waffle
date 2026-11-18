from __future__ import annotations

import random
from itertools import product

import pytest

from waffle.contracts.geometry.transform import Orientation, Point, Transform

ROTATIONS: tuple[Orientation, ...] = (0, 90, 180, 270)
ORIENTATIONS = [(m, r) for m, r in product((False, True), ROTATIONS)]


def random_transform(rng: random.Random) -> Transform:
    mirror, rotation = rng.choice(ORIENTATIONS)
    translation = (rng.randint(-10_000, 10_000), rng.randint(-10_000, 10_000))
    return Transform(mirror=mirror, rotation=rotation, translation=translation)


def random_point(rng: random.Random) -> Point:
    return (rng.randint(-10_000, 10_000), rng.randint(-10_000, 10_000))


def test_apply_order_is_mirror_then_rotate_then_translate() -> None:
    t = Transform(mirror=True, rotation=90, translation=(5, 5))
    # mirror (10, 2) -> (10, -2); rotate 90 ccw -> (2, 10); translate -> (7, 15)
    assert t.apply((10, 2)) == (7, 15)


def test_all_eight_orientations_apply_exactly() -> None:
    expected = {
        (False, 0): (3, 2),
        (False, 90): (-2, 3),
        (False, 180): (-3, -2),
        (False, 270): (2, -3),
        (True, 0): (3, -2),
        (True, 90): (2, 3),
        (True, 180): (-3, 2),
        (True, 270): (-2, -3),
    }
    assert set(expected) == set(ORIENTATIONS)
    for (mirror, rotation), image in expected.items():
        assert Transform(mirror=mirror, rotation=rotation).apply((3, 2)) == image


def test_composition_worked_example() -> None:
    outer = Transform(rotation=90, translation=(100, 0))
    inner = Transform(mirror=True, translation=(10, 0))
    composed = outer.compose(inner)
    assert composed == Transform(mirror=True, rotation=90, translation=(100, 10))
    assert composed.apply((1, 2)) == (102, 11) == outer.apply(inner.apply((1, 2)))


def test_composition_matches_sequential_application() -> None:
    rng = random.Random(1)
    for _ in range(500):
        outer, inner, p = random_transform(rng), random_transform(rng), random_point(rng)
        assert outer.compose(inner).apply(p) == outer.apply(inner.apply(p))


def test_composition_is_associative_with_identity() -> None:
    rng = random.Random(2)
    identity = Transform()
    for _ in range(500):
        a, b, c = random_transform(rng), random_transform(rng), random_transform(rng)
        assert a.compose(b).compose(c) == a.compose(b.compose(c))
        assert a.compose(identity) == a == identity.compose(a)


def test_composition_is_not_commutative() -> None:
    a = Transform(rotation=90)
    b = Transform(mirror=True)
    assert a.compose(b) != b.compose(a)


def test_inverse_round_trips_for_all_orientations() -> None:
    rng = random.Random(3)
    for mirror, rotation in ORIENTATIONS:
        for _ in range(50):
            t = Transform(mirror=mirror, rotation=rotation, translation=random_point(rng))
            p = random_point(rng)
            assert t.inverse().apply(t.apply(p)) == p
            assert t.apply(t.inverse().apply(p)) == p
            assert t.compose(t.inverse()) == Transform() == t.inverse().compose(t)


def test_direction_follows_the_linear_part() -> None:
    for mirror, rotation in ORIENTATIONS:
        t = Transform(mirror=mirror, rotation=rotation, translation=(7, -3))
        for angle in ROTATIONS:
            # a direction is the difference of two points one unit apart
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle]
            a, b = t.apply((0, 0)), t.apply((dx, dy))
            moved = (b[0] - a[0], b[1] - a[1])
            assert (
                t.apply_direction(angle)
                == {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}[moved]
            )


def test_unsupported_rotation_is_rejected_by_the_model() -> None:
    with pytest.raises(ValueError):
        Transform(rotation=45)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Transform(translation=(1.5, 0))  # type: ignore[arg-type]
