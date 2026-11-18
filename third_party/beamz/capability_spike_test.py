"""Bounded CPU field-propagation check against pinned BeamZ 0.6.2."""

from __future__ import annotations

import importlib.metadata

import beamz as bz
import numpy as np


def test_tm_pulse_propagates_in_positive_x() -> None:
    assert importlib.metadata.version("beamz") == "0.6.2"
    dx = 0.1 * bz.um
    dt = 0.1e-15
    time = np.arange(100) * dt
    signal = np.exp(-((time - 1.0e-15) / 0.35e-15) ** 2)
    frequency = bz.LIGHT_SPEED / (1.55 * bz.um)
    simulation = bz.Simulation(
        domain=(2.4 * bz.um, 1.2 * bz.um),
        resolution=dx,
        time=time,
        polarization="tm",
        # BeamZ's public domain is centered: x=-0.7 um maps to grid x=0.5 um.
        sources=[
            bz.GaussianSource(
                position=(-0.7 * bz.um, 0.0),
                width=0.15 * bz.um,
                signal=signal,
            )
        ],
        monitors=[
            bz.FieldRecorder(("Ez",), interval=5, name="field"),
            bz.FieldMonitor(
                center=(0.0, 0.0, 0.0),
                size=(0.0, 0.8 * bz.um, 0.0),
                freqs=np.array([frequency]),
                fields=("Ez",),
                name="dft",
            ),
            bz.FluxMonitor(
                center=(0.4 * bz.um, 0.0, 0.0),
                size=(0.0, 0.8 * bz.um, 1.0 * bz.um),
                freqs=np.array([frequency]),
                name="flux",
            ),
        ],
        boundaries=[bz.PML(thickness=0.2 * bz.um)],
        normalize_source=None,
    )
    result = simulation.run(backend="jax", performance=False)
    frames = result["field"].fields["Ez"]
    near = np.max(np.abs(frames[:, :, 8]), axis=1)
    far = np.max(np.abs(frames[:, :, 16]), axis=1)
    near_peak = int(np.argmax(near))
    far_peak = int(np.argmax(far))
    print(
        f"shape={frames.shape} times={result['field'].field_times.shape} "
        f"max={np.max(np.abs(frames))} near_peak={near_peak} far_peak={far_peak} "
        f"near={near.max()} far={far.max()}"
    )
    assert np.isfinite(frames).all()
    assert np.max(np.abs(frames)) > 0
    assert far_peak > near_peak
    assert far.max() > 0.01 * near.max()
    assert result.normalization_source is None
    assert result["dft"].dft_fields["Ez"].size > 0
    assert np.isfinite(result["dft"].dft_fields["Ez"]).all()
    assert np.max(np.abs(result["dft"].dft_fields["Ez"])) > 0
    flux = result["flux"].get_dft_flux()
    assert flux.size == 1
    assert np.isfinite(flux).all()
    assert np.max(np.abs(flux)) > 0
    print(f"dft_shape={result['dft'].dft_fields['Ez'].shape} flux={flux}")
