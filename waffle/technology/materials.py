"""Evaluate optical material models. No extrapolation: outside the declared validity
range the result is a diagnostic, never a number."""

from __future__ import annotations

import math

import numpy as np

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Dimension, Quantity
from waffle.contracts.technology.materials import ConstantIndex, Material, Sellmeier, Tabulated


def refractive_index(material: Material, wavelength: Quantity) -> Result[complex]:
    """The complex index `n + i*k` of `material` at a vacuum `wavelength`.

    Errors: `units.*` for a bad wavelength unit, `technology.material_out_of_range` outside
    `material.validity` (or, for tabulated data, outside the table), and
    `technology.material_nonphysical` when a Sellmeier sum gives `n^2 <= 0`.
    """
    subject = f"material:{material.name}"
    problems = [
        *wavelength.diagnostics(Dimension.LENGTH, subject=subject, location=("wavelength",)),
        *material.validity.diagnostics(subject, ("validity",)),
    ]
    if problems:
        return Result(None, tuple(problems))
    model = material.optical
    low, high = material.validity.minimum.si * 1e6, material.validity.maximum.si * 1e6
    if isinstance(model, Tabulated):
        low, high = max(low, model.wavelength_um[0]), min(high, model.wavelength_um[-1])
    um = wavelength.si * 1e6
    if not low <= um <= high:
        return _fail(
            "technology.material_out_of_range",
            f"{material.name} is valid for {low:g} to {high:g} um, not {um:g} um",
            subject,
        )
    match model:
        case ConstantIndex():
            return Result.of(complex(model.n, model.k))
        case Sellmeier():
            n_squared = 1.0 + sum(
                b * um**2 / (um**2 - c) for b, c in zip(model.b, model.c_um2, strict=True)
            )
            if n_squared <= 0:
                return _fail(
                    "technology.material_nonphysical",
                    f"Sellmeier n^2 = {n_squared:g} at {um:g} um",
                    subject,
                )
            return Result.of(complex(math.sqrt(n_squared), 0.0))
        case Tabulated():
            n = float(np.interp(um, model.wavelength_um, model.n))
            k = float(np.interp(um, model.wavelength_um, model.k))
            return Result.of(complex(n, k))


def _fail(code: str, message: str, subject: str) -> Result[complex]:
    return Result(
        None, (Diagnostic(severity=Severity.ERROR, code=code, message=message, subject=subject),)
    )
