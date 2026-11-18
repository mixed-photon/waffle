"""Canonical JSON serialization and content hashing.

Canonical form (stable across processes, platforms, and dict insertion order):

* UTF-8 JSON with sorted object keys, no insignificant whitespace, and
  non-ASCII characters emitted as-is.
* Accepted values: None, bool, int, float, str, list/tuple (both become arrays),
  and dicts with str keys. Anything else raises TypeError naming its location.
* ints are written exactly (arbitrary precision). floats use Python's shortest
  round-trip `repr`, so `1.0` stays `1.0` and is distinct from the int `1`.
  `-0.0` is normalized to `0.0`. NaN and infinities are rejected (ValueError)
  because JSON cannot represent them.

This is deterministic for Waffle's own use; it is not RFC 8785 (JCS).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

HASH_ALGORITHM = "sha256"


def _normalize(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float {value!r} at {path}")
        return value + 0.0  # turns -0.0 into 0.0
    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string key {key!r} at {path}")
            out[key] = _normalize(item, f"{path}.{key}")
        return out
    raise TypeError(f"unsupported type {type(value).__name__} at {path}")


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text of `value`."""
    return json.dumps(
        _normalize(value, "$"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(value: Any) -> str:
    """Return `sha256:<hex>` of the canonical JSON of `value`."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{HASH_ALGORITHM}:{digest}"
