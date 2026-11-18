"""Real CPU reduced-index propagation and raster orientation acceptance."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from third_party.beamz.adapter import BeamZBackend, rasterize
from waffle.contracts.analysis.request import (
    AnalysisRequest,
    BackendSettings,
    Excitation,
    NumericalSettings,
    Observable,
)
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.physics.problem import PhysicalProblem


def example() -> tuple[PhysicalProblem, AnalysisRequest]:
    from waffle.contracts.foundation.identity import EntityId
    from waffle.contracts.foundation.records import RevisionRef
    from waffle.contracts.geometry.shapes import Polygon
    from waffle.contracts.physics.approximations import (
        EffectiveIndexReduction,
        ReducedColumn,
        StackSegment,
    )
    from waffle.contracts.physics.problem import (
        Boundaries2D,
        BoundaryCondition,
        Domain2D,
        PortPlane,
        Reduced2D,
        Region2D,
    )

    def ref(kind: str) -> RevisionRef:
        return RevisionRef(EntityId(kind, "example"), "sha256:" + "0" * 64)

    segment = StackSegment(z_min_pm=0, z_max_pm=220000, material="silicon", index=2.4)
    problem = PhysicalProblem(
        id="physical_problem:wg",
        layout=ref("layout"),
        top_cell="wg",
        technology=ref("technology"),
        dbu_pm=1000,
        description=Reduced2D(
            domain=Domain2D(
                lo=(1000, 2000),
                hi=(7000, 4000),
                boundaries=Boundaries2D(
                    **{
                        f: BoundaryCondition(thickness_pm=400000)
                        for f in ("x_min", "x_max", "y_min", "y_max")
                    }
                ),
            ),
            background_index=1.44,
            regions=(
                Region2D(
                    layer="core",
                    index=2.4,
                    polygons=(
                        Polygon(
                            points=(
                                (1000, 2750),
                                (7000, 2750),
                                (7000, 3250),
                                (1000, 3250),
                            )
                        ),
                    ),
                ),
            ),
            ports=(
                PortPlane(
                    path="near",
                    layer="core",
                    domain="optical",
                    center=(3000, 3000),
                    width=1000,
                    orientation=0,
                ),
                PortPlane(
                    path="far",
                    layer="core",
                    domain="optical",
                    center=(5000, 3000),
                    width=1000,
                    orientation=0,
                ),
            ),
        ),
        approximations=(
            EffectiveIndexReduction(
                method="test",
                method_version=1,
                polarization="tm",
                mode_order=0,
                wavelength_pm=1550000,
                window_z_min_pm=0,
                window_z_max_pm=220000,
                columns=(
                    ReducedColumn(
                        layer=None,
                        segments=(segment,),
                        n_eff=1.44,
                        rule="cladding_cutoff",
                    ),
                    ReducedColumn(
                        layer="core", segments=(segment,), n_eff=2.4, rule="guided_mode"
                    ),
                ),
                validity="test-only reduced-index guide",
            ),
        ),
    )
    request = AnalysisRequest(
        id="analysis_request:run",
        problem=revision_of(problem),
        polarization="tm",
        excitations=(
            Excitation(kind="point", center_dbu=(1800, 3000), wavelength_pm=1550000),
        ),
        observables=(
            Observable(name="field", kind="raw_field", field_component="Ez"),
            Observable(
                name="near", kind="raw_field", field_component="Ez", port="near"
            ),
            Observable(name="far", kind="raw_field", field_component="Ez", port="far"),
        ),
        numerical=NumericalSettings(
            grid_spacing_pm=50000, time_step_as=100, steps=1600
        ),
        backend_settings=BackendSettings(
            backend="beamz",
            version="0.6.2",
            schema_version=1,
            parameters={"record_interval": 10},
        ),
    )
    return problem, request


def test_real_guide() -> None:
    problem, request = example()
    backend = BeamZBackend()
    run = backend.execute(problem, request)
    assert run.state.value == "complete", run.diagnostics
    field, near, far = run.acquisitions
    values = np.asarray(field.payload)
    near_history = np.max(np.abs(values[:, :, 40]), axis=1)
    far_history = np.max(np.abs(values[:, :, 80]), axis=1)
    print(
        "GUIDE",
        values.shape,
        "peak_steps",
        (np.argmax(near_history) + 1) * 10,
        (np.argmax(far_history) + 1) * 10,
        "DFT",
        np.max(abs(near.payload)),
        np.max(abs(far.payload)),
        "ratio",
        np.vdot(near.payload, far.payload) / np.vdot(near.payload, near.payload),
        "profile_residual",
        np.linalg.norm(
            far.payload
            - (np.vdot(near.payload, far.payload) / np.vdot(near.payload, near.payload))
            * near.payload
        )
        / np.linalg.norm(far.payload),
    )
    assert np.argmax(far_history) > np.argmax(near_history)
    assert np.max(np.abs(far.payload)) > 0
    epsilon = rasterize(problem, 50000)
    assert epsilon.shape == (40, 120)
    assert epsilon[20, 60] == 2.4**2
    assert epsilon[2, 60] == 1.44**2


def test_reject_before_solver() -> None:
    problem, request = example()
    backend = BeamZBackend()
    bad = request.model_copy(
        update={
            "numerical": request.numerical.model_copy(update={"time_step_as": 1000})
        }
    )
    assert backend.execute(problem, bad).state.value == "rejected"


def test_real_isolated_worker(tmp_path: Path) -> None:
    import os
    import subprocess
    from pathlib import Path

    from waffle.execution.protocol import WorkerRequest, read_response

    problem, request = example()
    (tmp_path / "request.json").write_text(
        WorkerRequest(problem=problem, request=request).model_dump_json()
    )
    child = subprocess.run(
        (str(Path(os.environ["BEAMZ_WORKER"]).resolve()), str(tmp_path)),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "JAX_PLATFORMS": "cpu"},
    )
    assert child.returncode == 0, child.stderr
    run, effective = read_response(tmp_path)
    assert run.state.value == "complete", run.diagnostics
    assert effective["beamz"] == "0.6.2"
    assert effective["monitors"]["near"]["plane_coordinate_m"] == 3e-6
    print("ISOLATED", run.state, effective["beamz"], effective["jax"])


def test_painter_order_and_shifted_orientation() -> None:
    problem, _request = example()
    region = problem.description.regions[0]
    later = region.model_copy(update={"index": 1.8})
    description = problem.description.model_copy(update={"regions": (region, later)})
    painted = rasterize(problem.model_copy(update={"description": description}), 50000)
    assert painted[20, 60] == 1.8**2
    assert painted[2, 60] == 1.44**2
    assert np.count_nonzero(painted[:, 60] == 1.8**2) == 10
    assert np.count_nonzero(painted[20, :] == 1.8**2) == 120


def test_preflight_rejects_wavelength_modes_settings_boundaries() -> None:
    problem, request = example()
    backend = BeamZBackend()
    excitation = request.excitations[0]
    for changes in ({"wavelength_pm": 1300000}, {"mode_order": 1}):
        rejected = request.model_copy(
            update={"excitations": (excitation.model_copy(update=changes),)}
        )
        assert backend.execute(problem, rejected).state.value == "rejected"
    settings = request.backend_settings.model_copy(
        update={"parameters": {"record_interval": "invalid"}}
    )
    assert (
        backend.execute(
            problem, request.model_copy(update={"backend_settings": settings})
        ).state.value
        == "rejected"
    )
    from waffle.contracts.physics.problem import BoundaryKind

    boundary = problem.description.domain.boundaries.x_min.model_copy(
        update={"kind": BoundaryKind.PEC, "thickness_pm": None}
    )
    boundaries = problem.description.domain.boundaries.model_copy(
        update={"x_min": boundary}
    )
    domain = problem.description.domain.model_copy(update={"boundaries": boundaries})
    modified = problem.model_copy(
        update={
            "description": problem.description.model_copy(update={"domain": domain})
        }
    )
    assert (
        backend.execute(
            modified, request.model_copy(update={"problem": revision_of(modified)})
        ).state.value
        == "rejected"
    )


def test_real_te_hz_support_and_units() -> None:
    from waffle.contracts.physics.approximations import Polarization

    problem, request = example()
    reduction = problem.approximations[0].model_copy(
        update={"polarization": Polarization.TE}
    )
    problem = problem.model_copy(update={"approximations": (reduction,)})
    request = request.model_copy(
        update={
            "problem": revision_of(problem),
            "polarization": Polarization.TE,
            "observables": tuple(
                o.model_copy(update={"field_component": "Hz"})
                for o in request.observables
            ),
        }
    )
    backend = BeamZBackend()
    run = backend.execute(problem, request)
    assert run.state.value == "complete", run.diagnostics
    field, near, far = run.acquisitions
    values = np.asarray(field.payload)
    assert values.shape == (160, 40, 120)
    assert all(a.units == "A/m" for a in run.acquisitions)
    assert np.argmax(np.max(abs(values[:, :, 80]), axis=1)) > np.argmax(
        np.max(abs(values[:, :, 40]), axis=1)
    )
    assert np.isclose(
        backend.last_metadata["monitors"]["near"]["plane_coordinate_m"],
        3.025e-6,
        rtol=0,
        atol=1e-20,
    )
    print(
        "TE",
        values.shape,
        "max_Hz",
        np.max(abs(values)),
        "near_far_DFT",
        np.max(abs(near.payload)),
        np.max(abs(far.payload)),
    )
