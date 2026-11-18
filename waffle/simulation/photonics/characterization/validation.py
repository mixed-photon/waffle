"""Independent reference and grid checks for exact transfer samples."""

from __future__ import annotations

from collections.abc import Mapping

from waffle.characterization.validation.compare import compare_complex
from waffle.contracts.characterization.report import ValidationReport
from waffle.simulation.photonics.characterization.waveguide import WaveguideEvidence


def validate_waveguide(
    fine: WaveguideEvidence,
    coarse: WaveguideEvidence,
    reference: Mapping[float, complex],
    *,
    report_id: str,
    reference_description: str,
    channels: tuple[str, ...],
    magnitude_tolerance: float = 0.05,
    phase_tolerance_rad: float = 0.15,
) -> ValidationReport:
    """Compare each fine sample to reference and independently acquired coarser grid.

    Does not measure interpolation error, estimate asymptotic order, or establish
    directional purity. A two-grid discrepancy is not a rigorous uncertainty bound.
    """
    if fine.grid_spacing_m >= coarse.grid_spacing_m:
        raise ValueError(
            "fine and coarse evidence require independently different grids"
        )
    if (
        fine.upstream_plane_m,
        fine.downstream_plane_m,
        fine.field_component,
        fine.output_channel,
        fine.excitation_channel,
        fine.time_convention,
    ) != (
        coarse.upstream_plane_m,
        coarse.downstream_plane_m,
        coarse.field_component,
        coarse.output_channel,
        coarse.excitation_channel,
        coarse.time_convention,
    ):
        raise ValueError(
            "grid comparisons require identical physical planes and conventions"
        )
    coarse_samples = {
        sample.frequency_hz: sample.amplitude for sample in coarse.samples
    }
    comparisons = []
    for sample in fine.samples:
        frequency = sample.frequency_hz
        for kind, target in (
            ("analytic_reference", reference[frequency]),
            ("grid_convergence", coarse_samples[frequency]),
        ):
            comparisons.extend(
                compare_complex(
                    sample.amplitude,
                    target,
                    frequency,
                    magnitude_tolerance=magnitude_tolerance,
                    phase_tolerance_rad=phase_tolerance_rad,
                    comparison=kind,
                )
            )
    pair = (fine.output_channel, fine.excitation_channel)
    return ValidationReport(
        id=report_id,
        procedure="uniform_guide_exact_samples",
        procedure_version="1",
        reference_description=reference_description,
        comparisons=tuple(comparisons),
        observed_channels=(pair,),
        uncomputed_channels=tuple(
            (out, excitation)
            for out in channels
            for excitation in channels
            if (out, excitation) != pair
        ),
        limitations=(
            *fine.limitations,
            "Two-grid comparison is not an uncertainty bound or time convergence test.",
        ),
    )
