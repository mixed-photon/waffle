"""Build macro for tests that run against the real gdsfactory."""

load("//tools:waffle.bzl", "waffle_py_test")

def gdsfactory_test(name, **kwargs):
    """A `waffle_py_test` on the pinned `@pip_gdsfactory` hub, tagged `backend` and `manual`.

    The tags keep `bazel test //...` from fetching or importing gdsfactory; run these through
    `//third_party/gdsfactory:all_backend_tests` or by name.

    Args:
      name: test name; the pytest file is `<name>.py`.
      **kwargs: forwarded to `waffle_py_test` (`deps`, `data`, `env`, ...).
    """
    waffle_py_test(
        name = name,
        size = "medium",
        pytest = "@pip_gdsfactory//pytest",
        tags = ["backend", "manual"],
        **kwargs
    )
