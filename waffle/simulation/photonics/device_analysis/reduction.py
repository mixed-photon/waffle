"""Effective-index reduction of a technology's vertical stack.

For each lateral kind of position (nothing drawn, or exactly one drawn layer) the technology
gives a vertical material column (`waffle.technology.stack.vertical_column`). The column is
cut to a vertical window, padded with the stack background where the stack does not reach
it, and its outermost segments are extended to infinity (substrate leakage and anything
beyond the window are ignored). The effective index is the fundamental (or requested) slab
mode of that column (`slab.effective_index`) at one vacuum wavelength, with every material
index evaluated there.

If a column guides no mode of the requested polarization and order, its effective index is
the larger of its two outermost indices, the limit of the mode at cutoff (rule
`cladding_cutoff`). This is the expected result for the background of a fully etched strip
(oxide everywhere); for a column with a drawn layer it also yields a warning, because the
waveguide then does not guide. Absorbing materials (`k > 0`) are rejected: this method is
lossless.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from waffle.contracts.foundation.diagnostics import Diagnostic, Severity, has_errors
from waffle.contracts.foundation.result import Result
from waffle.contracts.foundation.units import Quantity
from waffle.contracts.physics.approximations import (
    EffectiveIndexReduction,
    Polarization,
    ReducedColumn,
    StackSegment,
)
from waffle.contracts.technology.revision import TechnologyRevision
from waffle.simulation.photonics.device_analysis.slab import SlabLayer, SlabStack, effective_index
from waffle.technology.materials import refractive_index
from waffle.technology.stack import vertical_column

METHOD = "slab_transfer_matrix"
METHOD_VERSION = 1
VALIDITY = (
    "Scalar slab mode of the vertical stack at one vacuum wavelength: material and slab-mode "
    "dispersion are not represented, so the indices hold at that wavelength only. Lateral "
    "confinement, mode conversion at edges, sidewall slope, and radiation into layers beyond "
    "the window are ignored. Lossless materials only."
)
_PM = 1e-12


@dataclass(frozen=True)
class Window:
    """The vertical interval `[z_min_pm, z_max_pm]` the stacks are cut to."""

    z_min_pm: int
    z_max_pm: int


def reduce_stack(
    tech: TechnologyRevision,
    layers: Sequence[str],
    window: Window,
    wavelength_pm: int,
    polarization: Polarization,
    mode_order: int = 0,
) -> Result[EffectiveIndexReduction]:
    """The reduction with a column for the background and one per layer in `layers`.

    Layers must exist in the technology (`ValueError` otherwise, as for `vertical_column`).
    """
    diagnostics: list[Diagnostic] = []
    columns: list[ReducedColumn] = []
    for layer in (None, *layers):
        result = reduce_column(tech, layer, window, wavelength_pm, polarization, mode_order)
        diagnostics.extend(result.diagnostics)
        if result.value is not None:
            columns.append(result.value)
    if has_errors(diagnostics):
        return Result(None, tuple(diagnostics))
    return Result.of(
        EffectiveIndexReduction(
            method=METHOD,
            method_version=METHOD_VERSION,
            polarization=polarization,
            mode_order=mode_order,
            wavelength_pm=wavelength_pm,
            window_z_min_pm=window.z_min_pm,
            window_z_max_pm=window.z_max_pm,
            columns=tuple(columns),
            validity=VALIDITY,
        ),
        diagnostics,
    )


def reduce_column(
    tech: TechnologyRevision,
    layer: str | None,
    window: Window,
    wavelength_pm: int,
    polarization: Polarization,
    mode_order: int = 0,
) -> Result[ReducedColumn]:
    """Reduce the column where exactly `layer` (or nothing, for None) is drawn."""
    subject = str(tech.id)
    where = ("columns", layer or "background")
    segments = _window_segments(tech, layer, window)
    wavelength = Quantity(value=wavelength_pm / 1e6, unit="um")
    indices: dict[str, float] = {}
    problems: list[Diagnostic] = []
    for name in sorted({material for _, _, material in segments}):
        material = tech.material(name)
        if material is None:
            problems.append(
                _error(
                    "physics.reduction.material_unknown",
                    f"material {name!r} is not defined in the technology",
                    subject,
                    where,
                )
            )
            continue
        index = refractive_index(material, wavelength)
        problems.extend(index.diagnostics)
        if index.value is not None and index.value.imag > 0:
            problems.append(
                _error(
                    "physics.reduction.absorbing_material",
                    f"{name} has k = {index.value.imag:g}; the reduction is lossless",
                    subject,
                    where,
                )
            )
        elif index.value is not None:
            indices[name] = float(f"{index.value.real:.12g}")
    if problems:
        return Result(None, tuple(problems))

    stack_segments = tuple(
        StackSegment(z_min_pm=lo, z_max_pm=hi, material=m, index=indices[m])
        for lo, hi, m in segments
    )
    inner = tuple(SlabLayer((s.z_max_pm - s.z_min_pm) * _PM, s.index) for s in stack_segments[1:-1])
    stack = SlabStack(stack_segments[0].index, inner, stack_segments[-1].index)
    mode = effective_index(stack, wavelength_pm * _PM, polarization, mode_order)
    warnings: list[Diagnostic] = []
    rule: Literal["guided_mode", "cladding_cutoff"]
    if mode is None:
        n_eff, rule = max(stack.lower_index, stack.upper_index), "cladding_cutoff"
        if layer is not None:
            warnings.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="physics.reduction.no_guided_mode",
                    message=f"the column through layer {layer!r} guides no "
                    f"{polarization.value.upper()}{mode_order} mode; using the cladding index",
                    subject=subject,
                    location=where,
                )
            )
    else:
        n_eff, rule = mode, "guided_mode"
    return Result.of(
        ReducedColumn(
            layer=layer,
            segments=stack_segments,
            n_eff=float(f"{n_eff:.12g}"),
            rule=rule,
        ),
        warnings,
    )


def _window_segments(
    tech: TechnologyRevision, layer: str | None, window: Window
) -> list[tuple[int, int, str]]:
    """The column cut to `window` and padded with the background; adjacent equal materials
    merged. Technology z values are integer picometres (checked by the caller)."""
    column = [
        (round(s.z_min_m / _PM), round(s.z_max_m / _PM), s.material)
        for s in vertical_column(tech, () if layer is None else (layer,))
    ]
    background = tech.stack.background
    if not column:
        column = [(window.z_min_pm, window.z_max_pm, background)]
    column = [
        (window.z_min_pm, column[0][0], background),
        *column,
        (column[-1][1], window.z_max_pm, background),
    ]
    merged: list[tuple[int, int, str]] = []
    for lo, hi, material in column:
        lo, hi = max(lo, window.z_min_pm), min(hi, window.z_max_pm)
        if lo >= hi:
            continue
        if merged and merged[-1][2] == material:
            merged[-1] = (merged[-1][0], hi, material)
        else:
            merged.append((lo, hi, material))
    return merged


def _error(code: str, message: str, subject: str, location: tuple[str, ...]) -> Diagnostic:
    return Diagnostic(
        severity=Severity.ERROR, code=code, message=message, subject=subject, location=location
    )
