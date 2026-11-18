from __future__ import annotations

from waffle.contracts.foundation.diagnostics import Severity
from waffle.contracts.geometry.unsupported import Unsupported, unsupported


def test_every_unsupported_construct_has_a_stable_error_code() -> None:
    assert {u.value for u in Unsupported} == {
        "geometry.unsupported.curve",
        "geometry.unsupported.polygon_holes",
        "geometry.unsupported.shape",
        "geometry.unsupported.path_end",
        "geometry.unsupported.path_segment",
        "geometry.unsupported.rotation",
        "geometry.unsupported.magnification",
        "layout.unsupported.array_instance",
        "layout.unsupported.external_reference",
    }
    for construct in Unsupported:
        d = unsupported(construct, "met in cell 'x'", subject="s", location=("cells", "x"))
        assert (d.severity, d.code, d.subject, d.location) == (
            Severity.ERROR,
            construct.value,
            "s",
            ("cells", "x"),
        )
        assert d.message.endswith(": met in cell 'x'")
