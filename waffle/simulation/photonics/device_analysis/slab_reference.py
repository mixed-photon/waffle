"""Textbook slab dispersion relations, solved independently of `slab` (test helpers)."""

from __future__ import annotations

import math
from collections.abc import Callable

from waffle.contracts.physics.approximations import Polarization

TE = Polarization.TE


def bisect(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Root of a sign-changing `f` on `[lo, hi]`."""
    f_lo = f(lo)
    assert f_lo * f(hi) < 0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f_lo * f(mid) <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f(mid)
    return 0.5 * (lo + hi)


def textbook(
    n_lower: float,
    n_core: float,
    n_upper: float,
    d: float,
    wavelength: float,
    pol: Polarization,
    order: int = 0,
) -> float:
    """Asymmetric three-layer slab: kappa d = m pi + atan(a_l gamma_l / kappa)
    + atan(a_u gamma_u / kappa), with a = 1 (TE) or n_core^2 / n_clad^2 (TM)."""
    k0 = 2 * math.pi / wavelength
    n_low = max(n_lower, n_upper)

    def psi(n: float) -> float:
        kappa = k0 * math.sqrt(n_core**2 - n**2)
        total = kappa * d - order * math.pi
        for n_cl in (n_lower, n_upper):
            gamma = k0 * math.sqrt(max(n**2 - n_cl**2, 0.0))
            weight = 1.0 if pol is TE else n_core**2 / n_cl**2
            total -= math.atan(weight * gamma / kappa)
        return total

    return bisect(psi, n_low, n_core * (1 - 1e-12))


def symmetric_transcendental(
    n1: float, n2: float, d: float, wavelength: float, pol: Polarization
) -> float:
    """Even fundamental mode of a symmetric slab: kappa tan(kappa d/2) = gamma (TE) and
    (kappa / n1^2) tan(kappa d/2) = gamma / n2^2 (TM)."""
    k0 = 2 * math.pi / wavelength

    def h(n: float) -> float:
        kappa = k0 * math.sqrt(n1**2 - n**2)
        gamma = k0 * math.sqrt(n**2 - n2**2)
        scale = 1.0 if pol is TE else n1**-2 / n2**-2
        return scale * kappa * math.tan(kappa * d / 2) - gamma

    return bisect(h, n2 * (1 + 1e-12), n1 * (1 - 1e-12))
