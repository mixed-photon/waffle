"""Guided modes of a lossless planar multilayer slab (the 1D problem behind an effective
index).

Geometry: layers are normal to z; the mode propagates in the layer plane with propagation
constant `beta = k0 * n_eff` and is uniform in the other in-plane direction y. The stack is
two semi-infinite claddings (`lower`, `upper`) around finite `layers` listed bottom to top.

Polarization (field components for propagation along x):

* TE: the scalar field is `F = E_y`; `F` and `F'` are continuous across interfaces.
* TM: `F = H_y`; `F` and `F'/n^2` are continuous.

With `G = F'/p` (`p = 1` for TE, `p = n^2` for TM) a layer of index `n` and thickness `d`
maps `(F, G)` at its bottom to its top by

    [ cos(kz d)           p sin(kz d)/kz ]
    [ -kz sin(kz d)/p     cos(kz d)      ]        kz^2 = k0^2 (n^2 - n_eff^2)

(`cosh`/`sinh` for `kz^2 < 0`). The bottom cladding fixes `(F, G) = (1, gamma_l/p_l)` and the
top one requires `G + gamma_u/p_u F = 0`, with `gamma = k0 sqrt(n_eff^2 - n^2)`. That
residual is an entire function of `n_eff` (no poles, unlike a ratio of fields); its zeros in
`(max(n_lower, n_upper), max layer index)` are the guided modes. They are found by scanning
a grid uniform in the transverse wavenumber (where the zeros are roughly evenly spaced) for
sign changes and bisecting each. Mode order 0 is the largest `n_eff`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from waffle.contracts.physics.approximations import Polarization

_BISECTION_STEPS = 100
_GRID_POINTS_PER_MODE = 64


@dataclass(frozen=True)
class SlabLayer:
    thickness_m: float
    index: float


@dataclass(frozen=True)
class SlabStack:
    lower_index: float
    layers: tuple[SlabLayer, ...]
    upper_index: float


def effective_index(
    stack: SlabStack, wavelength_m: float, polarization: Polarization, mode_order: int = 0
) -> float | None:
    """`n_eff` of guided mode `mode_order` (0 is the fundamental), or None if the stack
    guides fewer modes of that polarization."""
    if mode_order < 0 or wavelength_m <= 0:
        raise ValueError("mode_order must be >= 0 and wavelength_m > 0")
    if not stack.layers:
        return None
    n_low = max(stack.lower_index, stack.upper_index)
    n_high = max(layer.index for layer in stack.layers)
    if n_high <= n_low:
        return None
    k0 = 2 * math.pi / wavelength_m
    span = math.sqrt(n_high**2 - n_low**2)
    total = sum(layer.thickness_m for layer in stack.layers)
    modes = int(k0 * total * span / math.pi) + 2  # upper bound on the guided modes
    points = _GRID_POINTS_PER_MODE * modes

    def residual_at(t: float) -> float:
        return _residual(stack, math.sqrt(max(n_high**2 - t * t, n_low**2)), k0, polarization)

    # t = sqrt(n_high^2 - n_eff^2): the mode with the largest n_eff has the smallest t.
    # The scan ends at the cutoff t = span, where n_eff = n_low and the residual is not a mode.
    found = -1
    t_previous, previous = 0.0, residual_at(0.0)
    for i in range(1, points + 1):
        t = span * i / points
        current = residual_at(t)
        if previous * current < 0:
            found += 1
            if found == mode_order:
                lo, hi = t_previous, t
                for _ in range(_BISECTION_STEPS):
                    mid = 0.5 * (lo + hi)
                    if previous * residual_at(mid) <= 0:
                        hi = mid
                    else:
                        lo = mid
                return math.sqrt(n_high**2 - (0.5 * (lo + hi)) ** 2)
        if current != 0:
            t_previous, previous = t, current
    return None


def _residual(stack: SlabStack, n_eff: float, k0: float, polarization: Polarization) -> float:
    def p(index: float) -> float:
        return index**2 if polarization is Polarization.TM else 1.0

    def decay(index: float) -> float:
        return k0 * math.sqrt(max(n_eff**2 - index**2, 0.0))

    field, flux = 1.0, decay(stack.lower_index) / p(stack.lower_index)
    for layer in stack.layers:
        kz2 = (k0 * k0) * (layer.index**2 - n_eff**2)
        pl, d = p(layer.index), layer.thickness_m
        if kz2 > 0:
            kz = math.sqrt(kz2)
            cos, sin_over_kz = math.cos(kz * d), math.sin(kz * d) / kz
        elif kz2 < 0:
            gamma = math.sqrt(-kz2)
            cos, sin_over_kz = math.cosh(gamma * d), math.sinh(gamma * d) / gamma
        else:
            cos, sin_over_kz = 1.0, d
        field, flux = (
            cos * field + pl * sin_over_kz * flux,
            -kz2 * sin_over_kz / pl * field + cos * flux,
        )
        # A positive rescale keeps the sign and the zeros and avoids overflow in thick layers.
        scale = math.hypot(field, flux / k0)
        field, flux = field / scale, flux / scale
    return flux + decay(stack.upper_index) / p(stack.upper_index) * field
