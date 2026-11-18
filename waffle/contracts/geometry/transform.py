"""Exact integer transforms: optional mirror, then rotation by a multiple of 90 degrees,
then translation.

`T.apply(p) = R(rotation) * M^mirror * p + translation`, where `M = diag(1, -1)` mirrors
about the x axis and `R` rotates counter-clockwise (y up). This is the GDS STRANS order.
Every coefficient is an integer, so composition and inversion are exact.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt

Point = tuple[StrictInt, StrictInt]
Orientation = Literal[0, 90, 180, 270]

_UNIT: dict[Orientation, Point] = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
_ANGLE: dict[Point, Orientation] = {v: k for k, v in _UNIT.items()}
# Row-major 2x2 integer matrix (a, b, c, d) = [[a, b], [c, d]].
_Matrix = tuple[int, int, int, int]


def _mul(m: _Matrix, x: int, y: int) -> tuple[int, int]:
    return (m[0] * x + m[1] * y, m[2] * x + m[3] * y)


class Transform(BaseModel):
    """A mirror/rotate/translate map. The default is the identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mirror: bool = False
    rotation: Orientation = 0
    translation: Point = (0, 0)

    def _matrix(self) -> _Matrix:
        c, s = _UNIT[self.rotation]
        return (c, s, s, -c) if self.mirror else (c, -s, s, c)

    @staticmethod
    def _from_matrix(m: _Matrix, translation: Point) -> Transform:
        return Transform(
            mirror=m[0] * m[3] - m[1] * m[2] < 0,
            rotation=_ANGLE[(m[0], m[2])],
            translation=translation,
        )

    def apply(self, point: Point) -> Point:
        x, y = _mul(self._matrix(), *point)
        return (x + self.translation[0], y + self.translation[1])

    def apply_direction(self, angle: Orientation) -> Orientation:
        """Where a direction (an angle in degrees) points after the linear part."""
        return _ANGLE[_mul(self._matrix(), *_UNIT[angle])]

    def compose(self, inner: Transform) -> Transform:
        """`self` after `inner`: `outer.compose(inner).apply(p) == outer.apply(inner.apply(p))`."""
        a, b, c, d = self._matrix()
        e, f, g, h = inner._matrix()
        product = (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h)
        dx, dy = _mul(self._matrix(), *inner.translation)
        return self._from_matrix(product, (dx + self.translation[0], dy + self.translation[1]))

    def inverse(self) -> Transform:
        """The transform undoing `self` (the matrix is orthogonal, so its inverse is its
        transpose)."""
        a, b, c, d = self._matrix()
        inverse = (a, c, b, d)
        x, y = _mul(inverse, *self.translation)
        return self._from_matrix(inverse, (-x, -y))
