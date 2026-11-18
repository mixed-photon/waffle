"""Separate coherent waves in two identical single-mode access leads."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result


@dataclass(frozen=True)
class LeadWaves:
    """Amplitudes at the reference plane, ordered by direction along x."""

    positive_x: complex
    negative_x: complex
    relative_residual: float


@dataclass(frozen=True)
class DirectionalFit:
    left: LeadWaves
    right: LeadWaves
    beta_rad_m: float


@dataclass(frozen=True)
class TwoPortEstimate:
    """Rows are outgoing, columns incident, both in left/right port order."""

    scattering: NDArray[np.complex128]
    incident_condition: float


def _failure[T](code: str, message: str) -> Result[T]:
    return Result(
        None, (Diagnostic(severity=Severity.ERROR, code=code, message=message),)
    )


def fit_directional_waves(
    offsets_m: NDArray[np.float64],
    projected_fields: NDArray[np.complex128],
    reference_beta_rad_m: float,
) -> Result[DirectionalFit]:
    """Fit a shared real beta and independent ±x waves at two reference planes.

    Rows are the two lead windows. Positions increase along global x, relative
    to each row's reference plane. Fields are coherent single-mode projections
    with the exp(-i omega t) convention and the same profile normalization.
    A shared phase constant does not constrain transmitted or reflected power.
    """
    if not (
        offsets_m.ndim == 2
        and offsets_m.shape[0] == 2
        and offsets_m.shape[1] >= 4
        and offsets_m.shape == projected_fields.shape
        and np.isfinite(offsets_m).all()
        and np.isfinite(projected_fields).all()
        and np.all(np.diff(offsets_m, axis=1) > 0)
        and np.isfinite(reference_beta_rad_m)
        and reference_beta_rad_m > 0
    ):
        return _failure(
            "waveguide.fit_input",
            "expected two ordered finite lead profiles and a positive reference beta",
        )
    scales = np.linalg.norm(projected_fields, axis=1)
    if not np.all(np.isfinite(scales)) or np.any(scales == 0):
        return _failure(
            "waveguide.fit_signal", "both leads need a nonzero coherent modal signal"
        )
    fields = projected_fields / scales[:, None]

    lower, upper = reference_beta_rad_m * 0.97, reference_beta_rad_m * 1.03
    lo, hi = lower, upper
    best_score = float("inf")
    best_fit = None
    try:
        for count in (241, 21, 21):
            candidates = np.linspace(lo, hi, count)
            for beta in candidates:
                wave = np.exp(1j * beta * offsets_m)
                basis = np.stack((wave, wave.conjugate()), axis=-1)
                a, b = (
                    np.linalg.lstsq(window, samples, rcond=None)
                    for window, samples in zip(basis, fields, strict=True)
                )
                if any(
                    result[2] != 2 or result[3][0] / result[3][-1] > 5
                    for result in (a, b)
                ):
                    continue
                coefficients = np.stack((a[0], b[0]))
                residual = np.linalg.norm(
                    (basis @ coefficients[..., None])[..., 0] - fields, axis=1
                )
                score = float(np.linalg.norm(residual))
                if score < best_score:
                    best_score = score
                    best_fit = (float(beta), coefficients * scales[:, None], residual)
            if best_fit is None:
                return _failure(
                    "waveguide.fit_rank",
                    "lead windows do not separate the two wave directions",
                )
            step = float(candidates[1] - candidates[0])
            lo, hi = max(lower, best_fit[0] - step), min(upper, best_fit[0] + step)
    except np.linalg.LinAlgError as exc:
        return _failure(
            "waveguide.fit_failed", f"directional least squares failed: {exc}"
        )
    beta, coefficients, residual = best_fit
    if min(beta - lower, upper - beta) < reference_beta_rad_m * 1e-6:
        return _failure(
            "waveguide.fit_search_limit",
            "fitted beta reached the limit of the qualified search range",
        )
    left, right = (
        LeadWaves(complex(row[0]), complex(row[1]), float(error))
        for row, error in zip(coefficients, residual, strict=True)
    )
    return Result.of(DirectionalFit(left, right, beta))


def estimate_two_port(
    first: DirectionalFit, second: DirectionalFit
) -> Result[TwoPortEstimate]:
    """Solve S from two independent launches at identical modal cross sections.

    Each launch measures incidence at both planes, including any residual
    field returning from the end boundary. Equal modal power factors cancel.
    """
    incident = np.asarray(
        [[run.left.positive_x, run.right.negative_x] for run in (first, second)]
    ).T
    outgoing = np.asarray(
        [[run.left.negative_x, run.right.positive_x] for run in (first, second)]
    ).T
    scales = np.linalg.norm(incident, axis=0)
    if (
        not np.isfinite(incident).all()
        or not np.isfinite(outgoing).all()
        or not np.isfinite(scales).all()
        or np.any(scales == 0)
    ):
        return _failure(
            "waveguide.incident_signal",
            "both launches need finite nonzero incident waves",
        )
    incident /= scales
    outgoing /= scales
    try:
        condition = float(np.linalg.cond(incident))
        if not np.isfinite(condition) or condition > 2:
            return _failure(
                "waveguide.incident_rank",
                "launches do not independently illuminate the two ports",
            )
        scattering = np.linalg.solve(incident.T, outgoing.T).T
    except np.linalg.LinAlgError as exc:
        return _failure(
            "waveguide.incident_solve", f"incident-wave solve failed: {exc}"
        )
    if not np.isfinite(scattering).all():
        return _failure(
            "waveguide.scattering_nonfinite", "directional estimate is nonfinite"
        )
    return Result.of(TwoPortEstimate(scattering, condition))
