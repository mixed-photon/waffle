"""A hash of the physical content of a `PhysicalProblem`.

`revision_of(problem)` changes with any edit. The fingerprint changes only when what a
solver would simulate changes, so a re-published but physically identical problem is
recognised (MOD-018).

Included: `dbu_pm`, the whole `description` (domain and boundary conditions, materials
without their `source`, films, prisms, regions, indices, ports with their layout port
paths) and the `approximations`.

Excluded: `id`, `metadata`, `schema_version`, the source references `layout`, `technology`
and `design`, `top_cell`, and `Material.source`. Because the references are excluded, two
technology revisions that differ only in descriptive text give equal fingerprints, while
any physical edit changes the description and therefore the fingerprint.
"""

from __future__ import annotations

from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.physics.problem import PhysicalProblem

_EXCLUDED = ("id", "metadata", "schema_version", "layout", "technology", "design", "top_cell")


def physical_problem_fingerprint(problem: PhysicalProblem) -> str:
    """`sha256:<hex>` over the physical content of `problem`."""
    payload = problem.model_dump(mode="json")
    for key in _EXCLUDED:
        del payload[key]
    for material in payload["description"].get("materials", ()):
        del material["source"]
    return content_hash(payload)
