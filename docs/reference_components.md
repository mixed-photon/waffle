# Independently distributed reference components

`distributions/waffle-pdk-demo`, `waffle-example-waveguide`, and `waffle-example-mmi`
are three independently versioned and licensed Python distributions. Their original
synthetic content is MIT licensed, carries no foundry data, and grants no fabrication
qualification. Release them separately; the core wheel does not contain these packages.
Restricted PDKs can use the same `Pdk`, `TechnologyRevision`, and component contracts
behind their own package repository and authorization boundary. No restricted content
belongs in this repository. Core tests retain their small redistributable fixtures.

Build the core and each distribution with `uv build --wheel <directory> --out-dir dist`.
Install the wheels in a fresh Python 3.13 environment with
`uv pip install --python /tmp/demo/bin/python --find-links dist waffle-example-waveguide==0.1.0 waffle-example-mmi==0.1.0`.
The demo PDK pins `waffle-eda[gdsfactory]==0.1.0`; each example pins
`waffle-pdk-demo==0.1.0`. Upstream GDSFactory is pinned by the core backend extra.
For a fully reproducible backend environment use the adapter's checked-in requirements.lock.
From a directory outside the checkout run:

```sh
python -m waffle_example_waveguide --output waveguide.gds
python -m waffle_example_mmi --output mmi.gds
```

Both examples generate real GDSFactory geometry through Waffle's backend API, export
GDS with its traceable sidecar, evaluate the analytic model through Waffle's pinned
evaluator dispatch, and resolve an exact one-instance circuit experiment binding.

```python
from waffle_pdk_demo import waveguide
reference = waveguide(length_um=10, wavelength_um=1.55)
response = reference.evaluate()   # all complex S[output, excitation] entries
circuit, binding = reference.bind()
reference.export("waveguide.gds")
# reference.snapshots() supplies all exact records for publish_binding/replay_binding.
```

The bundle exposes interface, component, implementation, resolved design, technology,
Layout, 2D PhysicalProblem, model, mapping, effective_index, wavelength_um and
polarization. Physics includes the exact design revision. Build a separate bundle for
each reduction wavelength: an existing physical problem's index reduction is fixed
at its recorded wavelength. Analytic evaluation supports 1.50–1.60 um and rejects
frequencies outside that range through applicability checks.

## Analytic conventions and limits

The original demo stack is a symmetric 220 nm silicon strip in silica, constant
material indices 3.476 and 1.444, 500 nm strip width, 1 nm dbu and 5 nm manufacturing
grid. Waveguide length is supplied through the adapter's validated geometry schema.
The reference uses the fundamental vertical planar slab mode, followed by the
fundamental lateral scalar slab mode of that reduced index distribution. This
is an effective-index approximation to 3D physics. The optional polarization chooses
the vertical TE/TM slab; the lateral scalar Hz problem for vertical TE uses TM continuity (H and H'/epsilon);
vertical TM maps to scalar Ez with TE continuity (E and E').
No claim of measured accuracy or numerical convergence is made.

Time dependence is exp(-i omega t). Transmission is exp(+i 2 pi n_eff L/lambda),
with reference planes at the exact top-level Layout ports and zero mapping offsets.
Amplitudes are power waves (squared magnitude is power), one mode per terminal.
The straight has S11=S22=0 and S12=S21 equal to that propagation phase. It is
lossless, reciprocal, and unitary by construction, without facet or roughness loss.
The analytic effective index is recomputed at each requested frequency; the
prepared simulation reduction remains pinned to its chosen wavelength.

`mmi()` generates the adapter's actual hierarchical 2x2 MMI, with fixed default
length/body/tapers and a 0.3 um gap to keep symmetric ports on the manufacturing
grid. In port order (o1,o2,o3,o4), the idealized matrix is
`exp(i beta L)/sqrt(2) * [[0,0,1,i],[0,0,i,1],[1,i,0,0],[i,1,0,0]]`,
where L=25.5 um is a declared common phase length, not a fitted multimode
propagation length. The body geometry does not determine this quadrature response:
self-imaging, dispersion, radiation, backreflection and imbalance are omitted.
It is an ideal reciprocal lossless four-port hybrid, with matched inputs and
outputs; it is not an extracted or validated electromagnetic MMI model.

Cross-component validation lives in `validation/demo_distribution`. Backend tests
are explicit/manual so the default core suite remains backend isolated.

## Recorded acceptance

On 2026-11-17, Python 3.13 on macOS arm64 passed:

```sh
bazel test //validation/demo_distribution:reference_test
uv build --wheel --out-dir /tmp/waffle-reference-wheels
uv build --wheel distributions/waffle-pdk-demo --out-dir /tmp/waffle-reference-wheels
uv build --wheel distributions/waffle-example-waveguide --out-dir /tmp/waffle-reference-wheels
uv build --wheel distributions/waffle-example-mmi --out-dir /tmp/waffle-reference-wheels
uv pip install --python /tmp/waffle-reference-venv/bin/python --find-links /tmp/waffle-reference-wheels waffle-example-waveguide==0.1.0 waffle-example-mmi==0.1.0
```

With working directory `/tmp/waffle-installed-examples`, both commands below passed;
imports were verified under the environment's `site-packages`, outside the checkout:

```sh
env -u PYTHONPATH /tmp/waffle-reference-venv/bin/python -m waffle_example_waveguide --output waveguide.gds
env -u PYTHONPATH /tmp/waffle-reference-venv/bin/python -m waffle_example_mmi --output mmi.gds
```

The default analytic lateral effective index is 2.491433230518291 at 1.55 um;
the recorded vertical reduction core index is 2.84778224345. Integration tests
exercise full-matrix reciprocity and unitarity, straight transmission magnitude and
phase, rejected out-of-band evaluation, actual GDS semantic round trip, and exact
experiment snapshot publication/replay. These establish implementation consistency,
not agreement with physical measurements.
