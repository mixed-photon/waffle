"""Inspectable operation-specific equivalence, separate from exact replay identities.

These keys do not implement a cache. A matching key is insufficient to load a run:
completed state, artifact integrity, output schema and exact record association still
need checking by any future hydration implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from waffle.contracts.analysis.request import AnalysisRequest
from waffle.contracts.component.parameters import OperatingValues
from waffle.contracts.foundation.canonical import content_hash
from waffle.contracts.foundation.identity import RevisionRef
from waffle.contracts.foundation.records import revision_of
from waffle.contracts.mappings.mapping import ViewMapping
from waffle.contracts.models.model import ModelRevision
from waffle.contracts.physics.problem import PhysicalProblem
from waffle.simulation.common.fingerprint import physical_problem_fingerprint


@dataclass(frozen=True)
class RunFingerprint:
    digest: str
    components: tuple[tuple[str, str], ...]
    problem: RevisionRef
    request: RevisionRef


@dataclass(frozen=True)
class FingerprintComparison:
    numerically_equivalent: bool
    exact_replay_identity: bool
    changed_components: tuple[str, ...]
    reason: str


def run_fingerprint(
    problem: PhysicalProblem,
    request: AnalysisRequest,
    *,
    operation_version: str,
    effective_environment: Mapping[str, str],
) -> RunFingerprint:
    """Fingerprint bounded electromagnetic execution after worker environment discovery.

    Environment must describe effective worker interpreter, upstream libraries,
    adapter, platform and precision/device configuration. Launcher paths alone are
    not an environment identity. All supplied entries affect the key. Resource
    limits are included conservatively because they can change run completeness.
    Mapping is retained as an exact pin: no unsupported equivalence is inferred.
    """
    if request.problem != revision_of(problem):
        raise ValueError("analysis request does not pin the supplied physical problem")
    if not operation_version or not effective_environment:
        raise ValueError("operation version and effective environment are required")
    if any(not key or not value for key, value in effective_environment.items()):
        raise ValueError("effective environment entries must be nonempty")
    parts = {
        "physics": physical_problem_fingerprint(problem),
        "operation": content_hash(
            {"name": "electromagnetic.execute", "version": operation_version}
        ),
        "numerical": content_hash(request.numerical.model_dump(mode="json")),
        "excitations": content_hash(
            [e.model_dump(mode="json") for e in request.excitations]
        ),
        "observables": content_hash(
            [o.model_dump(mode="json") for o in request.observables]
        ),
        "backend": content_hash(request.backend_settings.model_dump(mode="json")),
        "environment": content_hash(dict(effective_environment)),
        "polarization": content_hash(request.polarization),
        "mapping": content_hash(str(request.mapping) if request.mapping else None),
        "resources": content_hash(request.resources.model_dump(mode="json")),
        "schema": content_hash({"fingerprint": 1, "analysis": request.schema_version}),
    }
    return RunFingerprint(
        content_hash(parts),
        tuple(sorted(parts.items())),
        revision_of(problem),
        revision_of(request),
    )


def compare_run_fingerprints(
    previous: RunFingerprint, current: RunFingerprint
) -> FingerprintComparison:
    before, after = dict(previous.components), dict(current.components)
    changed = tuple(
        sorted(
            key
            for key in before.keys() | after.keys()
            if before.get(key) != after.get(key)
        )
    )
    equal = previous.digest == current.digest and not changed
    exact = previous.problem == current.problem and previous.request == current.request
    reason = (
        "numerical inputs match; cache hydration is not implemented"
        if equal
        else "changed numerical dependencies: " + ", ".join(changed)
    )
    return FingerprintComparison(equal, exact, changed, reason)


def model_evaluation_fingerprint(
    model: ModelRevision, operating: OperatingValues, mapping: ViewMapping
) -> str:
    """Conservative analytic operation key; coefficients and evaluator metadata matter.

    Unlike physical preparation, model metadata may be executable evaluator inputs.
    Keep all model claims/evidence and exact mapping pins rather than guessing which
    evaluator fields are descriptive. Never substitutes for applicability checks.
    """
    payload = model.model_dump(mode="json")
    payload.pop("id")
    return content_hash(
        {
            "operation": "optical.evaluate.v1",
            "model": payload,
            "operating": operating.model_dump(mode="json"),
            "mapping": mapping.model_dump(mode="json"),
        }
    )
