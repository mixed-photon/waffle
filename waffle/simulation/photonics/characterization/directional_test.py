from __future__ import annotations

import numpy as np

from waffle.simulation.photonics.characterization.directional import (
    estimate_two_port,
    fit_directional_waves,
)


def test_standing_waves_do_not_bias_directional_transmission() -> None:
    beta = 7.5e6
    length = np.pi / (2 * beta)
    x = np.asarray([-1.605, -1.241, -0.921, -0.614, -0.31, -0.185, 0, 0.251]) * 1e-6
    offsets = np.stack((x, -x[::-1]))
    wave = np.exp(1j * beta * offsets)
    transmission = np.exp(1j * beta * length)
    # Proportions are chosen so the total field at the right reference plane
    # has magnitude 1.1 and the total at the left has magnitude 0.9.
    field = np.stack(
        (
            wave[0] - 0.1 * wave[0].conjugate(),
            transmission * wave[1] + 0.1 * transmission * wave[1].conjugate(),
        )
    )
    fit = fit_directional_waves(offsets, field, beta).unwrap()
    total_ratio = (fit.right.positive_x + fit.right.negative_x) / (
        fit.left.positive_x + fit.left.negative_x
    )
    assert abs(total_ratio) > 1.2
    assert abs(fit.right.positive_x / fit.left.positive_x - transmission) < 1e-9


def test_two_launches_remove_cross_incidence_without_forcing_passivity() -> None:
    beta = 7.49e6
    x = np.asarray([-1.58, -1.25, -0.91, -0.61, -0.38, -0.2, 0.1, 0.25]) * 1e-6
    offsets = np.stack((x, -x[::-1]))
    wave = np.exp(1j * beta * offsets)
    expected = np.asarray([[0.02j, 1.01j], [0.8 + 0.1j, -0.04j]])
    incident = np.asarray([[1 + 0.2j, 0.06j], [0.03j, 0.8 - 0.1j]])
    outgoing = expected @ incident
    fits = tuple(
        fit_directional_waves(
            offsets,
            np.stack(
                (
                    incident[0, i] * wave[0] + outgoing[0, i] * wave[0].conjugate(),
                    outgoing[1, i] * wave[1] + incident[1, i] * wave[1].conjugate(),
                )
            ),
            7.5e6,
        ).unwrap()
        for i in range(2)
    )
    estimate = estimate_two_port(*fits).unwrap()
    np.testing.assert_allclose(estimate.scattering, expected, atol=3e-5, rtol=0)
    assert abs(estimate.scattering[0, 1]) > 1
    assert {d.code for d in estimate_two_port(fits[0], fits[0]).diagnostics} == {
        "waveguide.incident_rank"
    }
