"""Analysis-time dependency boundary check for core libraries."""

FORBIDDEN_PACKAGES = ["third_party"]  # main-repo packages core may not reach
FORBIDDEN_REPOS = ["pip_gdsfactory", "pip_beamz"]  # optional backend pip hubs

_Closure = provider(fields = ["labels"])

def _closure_impl(target, ctx):
    label = target.label
    transitive = [d[_Closure].labels for d in getattr(ctx.rule.attr, "deps", []) if _Closure in d]
    return [_Closure(labels = depset(
        [struct(repo = label.repo_name, package = label.package, text = str(label))],
        transitive = transitive,
    ))]

_closure_aspect = aspect(implementation = _closure_impl, attr_aspects = ["deps"])

def _forbidden_area(entry):
    """Returns the forbidden area `entry` belongs to (`//third_party` or a hub), else None."""
    if not entry.repo:
        for p in FORBIDDEN_PACKAGES:
            if entry.package == p or entry.package.startswith(p + "/"):
                return "//" + p
        return None
    for r in FORBIDDEN_REPOS:
        if r in entry.repo:
            return "@" + r
    return None

def _boundary_test_impl(ctx):
    entries = ctx.attr.target[_Closure].labels.to_list()
    areas = {}
    for e in entries:
        area = _forbidden_area(e)
        if area:
            areas[area] = areas.get(area, 0) + 1
    violations = ["%s (%d targets)" % (a, n) for a, n in sorted(areas.items())]
    lines = ["#!/usr/bin/env bash"]
    if violations:
        lines.append("echo 'Forbidden dependencies of %s:'" % ctx.attr.target.label)
        lines += ["echo '  %s'" % v for v in violations]
        lines.append("exit 1")
    else:
        lines.append("echo 'OK: %d transitive targets, none forbidden'" % len(entries))
    script = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(script, "\n".join(lines) + "\n", is_executable = True)
    return [DefaultInfo(executable = script)]

boundary_test = rule(
    implementation = _boundary_test_impl,
    attrs = {"target": attr.label(aspects = [_closure_aspect], mandatory = True)},
    test = True,
    doc = "Fails if the target's transitive deps include //third_party or a backend pip hub.",
)
