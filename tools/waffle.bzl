"""Build macros for Waffle Python code.

    waffle_py_library  py_library for //waffle code; also registers a boundary test.
    waffle_py_test     py_test that runs a pytest file; adding a test is one line.
"""

load("@rules_python//python:py_library.bzl", "py_library")
load("@rules_python//python:py_test.bzl", "py_test")
load(":boundary.bzl", "boundary_test")

CORE_PYTEST = "@pip_core//pytest"

def waffle_py_library(name, srcs, deps = [], **kwargs):
    """A py_library whose transitive closure must stay free of optional backends.

    Args:
      name: library name; `<name>_boundary_test` is created alongside it.
      srcs: Python sources.
      deps: library dependencies.
      **kwargs: forwarded to py_library (set `visibility` explicitly).
    """
    py_library(name = name, srcs = srcs, deps = deps, **kwargs)
    boundary_test(name = name + "_boundary_test", target = ":" + name, size = "small")

def waffle_py_test(name, srcs = None, deps = [], data = [], args = [], pytest = CORE_PYTEST, **kwargs):
    """Runs pytest over `srcs` (default: `<name>.py`) as a Bazel test.

    Args:
      name: test name; by convention `<module>_test`.
      srcs: pytest files; defaults to `[name + ".py"]`.
      deps: libraries under test and helpers.
      data: runtime data files.
      args: extra pytest arguments.
      pytest: pytest dependency; backend smoke tests pass their own hub's pytest.
      **kwargs: forwarded to py_test (e.g. `tags`, `size`).
    """
    srcs = srcs or [name + ".py"]
    py_test(
        name = name,
        srcs = srcs + ["//tools:pytest_main.py"],
        main = "//tools:pytest_main.py",
        args = ["$(rootpath %s)" % s for s in srcs] + args,
        data = data,
        deps = deps + [pytest],
        **kwargs
    )
