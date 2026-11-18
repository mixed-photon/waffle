"""The slab solver against textbook equations solved independently here, a finite-difference
eigenproblem, and limiting cases."""

from __future__ import annotations

import math

import numpy as np
import pytest

from waffle.contracts.foundation.units import Quantity
from waffle.contracts.physics.approximations import Polarization
from waffle.pdk.testing.synthetic import synthetic_soi
from waffle.simulation.photonics.device_analysis.slab import SlabLayer, SlabStack, effective_index
from waffle.simulation.photonics.device_analysis.slab_reference import (
    symmetric_transcendental,
    textbook,
)
from waffle.technology.materials import refractive_index

TE, TM = Polarization.TE, Polarization.TM
UM = 1e-6


def silicon_in_oxide() -> tuple[float, float]:
    tech = synthetic_soi()
    wavelength = Quantity(value=1.55, unit="um")
    indices = []
    for name in ("si", "sio2"):
        material = tech.material(name)
        assert material is not None
        indices.append(refractive_index(material, wavelength).unwrap().real)
    return indices[0], indices[1]


def test_220nm_silicon_in_oxide_matches_the_transcendental_equation() -> None:
    si, sio2 = silicon_in_oxide()
    assert si == 3.476 and sio2 == pytest.approx(1.444, abs=1e-3)
    stack = SlabStack(sio2, (SlabLayer(220e-9, si),), sio2)
    te0 = effective_index(stack, 1.55 * UM, TE)
    tm0 = effective_index(stack, 1.55 * UM, TM)
    assert te0 is not None and tm0 is not None
    assert te0 == pytest.approx(symmetric_transcendental(si, sio2, 220e-9, 1.55 * UM, TE), abs=1e-9)
    assert tm0 == pytest.approx(symmetric_transcendental(si, sio2, 220e-9, 1.55 * UM, TM), abs=1e-9)
    assert 2.8 <= te0 <= 2.85
    assert sio2 < tm0 < te0


def test_te_fundamental_exceeds_tm_for_a_high_contrast_slab() -> None:
    si, sio2 = silicon_in_oxide()
    for d in (100e-9, 220e-9, 400e-9):
        stack = SlabStack(sio2, (SlabLayer(d, si),), sio2)
        te, tm = effective_index(stack, 1.55 * UM, TE), effective_index(stack, 1.55 * UM, TM)
        assert te is not None and tm is not None and te > tm


@pytest.mark.parametrize("pol", [TE, TM])
def test_asymmetric_slab_orders_match_the_textbook_dispersion_relation(pol: Polarization) -> None:
    n_lower, n_core, n_upper, d, wavelength = 1.444, 3.0, 1.0, 1.5 * UM, 1.55 * UM
    stack = SlabStack(n_lower, (SlabLayer(d, n_core),), n_upper)
    values: list[float] = []
    for m in range(4):
        value = effective_index(stack, wavelength, pol, m)
        assert value is not None
        assert value == pytest.approx(textbook(n_lower, n_core, n_upper, d, wavelength, pol, m))
        values.append(value)
    assert values == sorted(values, reverse=True)
    assert effective_index(stack, wavelength, pol, 40) is None


def test_thick_core_approaches_the_core_index() -> None:
    stack = SlabStack(1.444, (SlabLayer(50 * UM, 3.476),), 1.444)
    te0 = effective_index(stack, 1.55 * UM, TE)
    assert te0 is not None and 3.476 - 1e-4 < te0 < 3.476
    assert te0 == pytest.approx(textbook(1.444, 3.476, 1.444, 50 * UM, 1.55 * UM, TE), abs=1e-9)


def test_cutoff_of_a_higher_order_mode_in_an_asymmetric_slab() -> None:
    n_s, n_core, n_c, wavelength = 1.444, 3.0, 1.0, 1.55 * UM
    k0 = 2 * math.pi / wavelength
    root = math.sqrt(n_core**2 - n_s**2)
    cutoff = (math.pi + math.atan(math.sqrt((n_s**2 - n_c**2) / root**2))) / (k0 * root)
    below = SlabStack(n_s, (SlabLayer(0.99 * cutoff, n_core),), n_c)
    above = SlabStack(n_s, (SlabLayer(1.01 * cutoff, n_core),), n_c)
    assert effective_index(below, wavelength, TE, 1) is None
    mode = effective_index(above, wavelength, TE, 1)
    assert mode is not None and n_s < mode < n_s + 0.02
    assert effective_index(below, wavelength, TE, 0) is not None


def test_a_symmetric_slab_guides_its_fundamental_mode_at_any_thickness() -> None:
    stack = SlabStack(1.444, (SlabLayer(1e-9, 3.476),), 1.444)
    n = effective_index(stack, 1.55 * UM, TE)
    assert n == pytest.approx(textbook(1.444, 3.476, 1.444, 1e-9, 1.55 * UM, TE), abs=1e-9)
    assert n is not None and n > 1.444


def test_no_guided_mode_without_a_core() -> None:
    assert effective_index(SlabStack(1.444, (), 1.444), 1.55 * UM, TE) is None
    uniform = SlabStack(1.444, (SlabLayer(1 * UM, 1.444),), 1.444)
    assert effective_index(uniform, 1.55 * UM, TE) is None
    lower_core = SlabStack(2.0, (SlabLayer(1 * UM, 1.5),), 1.0)
    assert effective_index(lower_core, 1.55 * UM, TM) is None


def test_the_stack_is_not_orientation_dependent() -> None:
    layers = (SlabLayer(0.1 * UM, 2.0), SlabLayer(0.3 * UM, 3.4), SlabLayer(0.2 * UM, 2.5))
    up = SlabStack(1.444, layers, 1.0)
    down = SlabStack(1.0, tuple(reversed(layers)), 1.444)
    for pol in (TE, TM):
        a, b = effective_index(up, 1.55 * UM, pol), effective_index(down, 1.55 * UM, pol)
        assert a is not None and b is not None
        assert a == pytest.approx(b, abs=1e-12)


def test_multilayer_te_matches_a_finite_difference_eigenproblem() -> None:
    # Rib-like column: oxide | 90 nm Si | 130 nm Si-oxide mix | 60 nm Si | air.
    layers = (
        SlabLayer(90e-9, 3.476),
        SlabLayer(130e-9, 2.2),
        SlabLayer(60e-9, 3.476),
    )
    lower, upper, wavelength = 1.444, 1.0, 1.55 * UM
    stack = SlabStack(lower, layers, upper)
    h = 2e-9
    margin = 1.2e-6
    edges = np.cumsum([0.0, *[layer.thickness_m for layer in layers]])
    z = np.arange(-margin, edges[-1] + margin, h) + h / 2
    n = np.full_like(z, lower)
    n[z >= edges[-1]] = upper
    for layer, lo, hi in zip(layers, edges[:-1], edges[1:], strict=True):
        n[(z >= lo) & (z < hi)] = layer.index
    k0 = 2 * math.pi / wavelength
    operator = (
        np.diag(k0**2 * n**2 - 2 / h**2)
        + np.diag(np.full(len(z) - 1, 1 / h**2), 1)
        + np.diag(np.full(len(z) - 1, 1 / h**2), -1)
    )
    beta_squared = np.linalg.eigvalsh(operator)[-1]
    reference = math.sqrt(beta_squared) / k0
    assert effective_index(stack, wavelength, TE) == pytest.approx(reference, abs=5e-4)


def test_invalid_arguments_are_programmer_errors() -> None:
    stack = SlabStack(1.444, (SlabLayer(220e-9, 3.476),), 1.444)
    with pytest.raises(ValueError, match="mode_order"):
        effective_index(stack, 1.55 * UM, TE, -1)
    with pytest.raises(ValueError, match="wavelength"):
        effective_index(stack, 0.0, TE)
