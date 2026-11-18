# Installed reference workflow

The local 0.1.0 release builds four wheels. Waffle provides the contracts, APIs,
CLI, and optional adapter code. `waffle-pdk-demo` contains the synthetic process
and device definitions. `waffle-example-waveguide` and `waffle-example-mmi` are
separately installable applications that use those public interfaces. The
reference workflow was verified with Python 3.13 on macOS arm64 and in a Debian
Linux arm64 container. The linked acceptance records predate the Waffle rename.
The BeamZ worker uses JAX CPU.
The worker records effective environment versions, enforces a wall time limit,
and checks a memory estimate before allocating its raster and recorders.

From the source checkout, build a local wheel directory. The Bazel launcher
runs `uv build` for each package.

```sh
bazel run //tools:release_wheels -- --out dist/wheels
wheelhouse="$(pwd)/dist/wheels"
demo_dir="$(mktemp -d)"
cd "$demo_dir"
uv venv --python 3.13 designer
uv venv --python 3.13 solver
uv pip install --python designer/bin/python --find-links "$wheelhouse" \
  'waffle-example-waveguide==0.1.0' 'waffle-example-mmi==0.1.0'
uv pip install --python solver/bin/python --find-links "$wheelhouse" \
  'waffle-eda[beamz]==0.1.0'
designer/bin/waffle doctor --require gdsfactory
solver/bin/waffle doctor --require beamz
```

The recorded Linux verification used a `python:3.13-slim` arm64 container.
Install this tested runtime package set on that Debian image
before running the examples. A bare slim image lacked `libexpat.so.1` for
KLayout. The package installation check alone cannot detect that loader error.

```sh
apt-get update
apt-get install -y --no-install-recommends \
  libexpat1 libgl1 libglib2.0-0t64 libxkbcommon-x11-0 libxcb-cursor0 \
  libfontconfig1 libfreetype6 libgomp1
```

The installed Linux check generated both GDS examples, ran the calibrated
waveguide through a separate solver environment, and replayed the saved
response exactly. [Linux run evidence](evidence/linux_arm64_reference_0.1.0.json)

The designer environment can generate GDS and evaluate analytic models on its
own. Run the waveguide or MMI example from a directory where you want its GDS
and associated port sidecar.

```sh
designer/bin/python -m waffle_example_waveguide
designer/bin/python -m waffle_example_mmi
designer/bin/waffle reference --model analytic --store ./results-analytic
```

The straight-waveguide example uses a 2 µm TM reduced guide at 1550 nm.
The `simulated` option runs ten isolated BeamZ attempts across both launches
and publishes all four complex scattering responses when the numerical
checks pass. The `calibrated` option runs the four-attempt forward recipe and
fits attenuation for that forward response using separate synthetic fitting
and validation captures. Both options publish checked GDS with port metadata
and exact experiment bindings. Failed qualification retains the numerical
evidence and reports its reference in the diagnostics.

```sh
designer/bin/waffle reference --model simulated --store ./results \
  --beamz-python "$demo_dir/solver/bin/python" > report.json
```

`--beamz-python` selects the environment for the isolated worker. A compatible
Bazel worker from `bazel build //third_party/beamz:worker` can be selected with
`--beamz-worker` instead. If you install both backend extras in the designer
environment, its own Python is the default worker interpreter.

The JSON report includes the exact design, layout, physics, model and mapping
revisions and refers to artifacts in `results/artifacts`. The original worker
inputs, logs, and completion status are in `results/workers/<attempt-id>`.
For `simulated`, use `references.transfer` for the directional estimates and
`references.validation` for the qualification report. The four published
responses appear in `replayed_responses` with explicit output and excitation
channels.
Artifact references are printed as `kind@sha256:<digest>`. Use the exact
`references.selected_binding` value to check integrity and reevaluate the
persisted experiment. Replay uses the recorded evaluator version, conditions,
and response pairs for each instance.

```sh
binding=$(designer/bin/python -c \
  'import json; print(json.load(open("report.json"))["references"]["selected_binding"])')
designer/bin/waffle replay --store ./results "$binding"
```

Bindings pin the evaluator names and artifact references of the release that
wrote them. To replay a binding recorded before the Waffle rename, use the
original wheels and artifact store.

For Python callers, `waffle_pdk_demo.waveguide` and `mmi` expose the resolved
design, layout, technology, physical problem, analytic model and mapping.
`waffle.apps.cli.lifecycle.run_reference` assembles the headless workflow.
`reproduce_experiment` takes an artifact store and a binding reference, and
returns the pinned per-instance complex responses and diagnostics. The backend
is selected by application assembly. The core and its fast tests remain
independent of optional solver installations.

The numerical MMI example uses the Python `characterize_mmi` entry point and the
same isolated worker. Its explicit acceptance target publishes an exact binding
that the installed `waffle replay` can read. [MMI characterization](mmi_characterization.md)

For numerical and inference limits, see [waveguide characterization](waveguide_characterization.md),
[calibration](calibration.md), and [BeamZ execution](beamz_execution.md).
The [lifecycle report](lifecycle_report.md) records acceptance evidence from the
installed run.
