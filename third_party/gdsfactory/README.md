# gdsfactory

Layout backend adapter (MOD-005). It implements all four capabilities of
`//waffle/layout/backend:interface`: generation (`waffle.gdsfactory.straight`, `mmi1x2`,
`mmi2x2`), GDS import, GDS export with a sidecar, and polygon merge and Boolean operations.
Design, conventions, generators, and known limits: `docs/layout_backends.md`.

* Upstream: <https://pypi.org/project/gdsfactory/>, MIT license (shipped `LICENSE`).
* Pinned: `gdsfactory==9.51.0`, with kfactory 3.0.4 and klayout 0.30.13 from
  `requirements.lock`. Upstream supports Python `>=3.12,<3.15`.
* Hub: `@pip_gdsfactory`. Reach it only through `//third_party/gdsfactory:upstream`; adapter
  targets are `manual` so `bazel build //...` never fetches it.
* Core pydantic (`requirements/core.in`) is capped below 2.14 because kfactory 3.0.4
  requires it.
* Tests (tags `backend`, `manual`; the first run downloads about 100 wheels):

  ```sh
  bazel test //third_party/gdsfactory:all_backend_tests   # smoke, backend, convert, session
  bazel test //third_party/gdsfactory:backend_test        # acceptance and shared conformance
  ```

* Modules: `backend.py` (`GdsfactoryBackend`), `generators.py`, `convert.py`, `gds.py`,
  `geometry.py`, `session.py` (isolation from gdsfactory's global state); `fixtures.py` is
  test support.
* To change the pin, edit `requirements.in` and run `tools/lock.sh`.
