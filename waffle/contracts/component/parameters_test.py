from __future__ import annotations

import pytest
from pydantic import ValidationError

from waffle.contracts.component.parameters import (
    DesignParameterSchema,
    DesignValues,
    FittedCoefficients,
    OperatingConditionSchema,
    OperatingValues,
    ParameterSpec,
)
from waffle.contracts.foundation.units import Dimension, Quantity


def q(value: float, unit: str) -> Quantity:
    return Quantity(value=value, unit=unit)


SCHEMA = DesignParameterSchema(
    parameters=(
        ParameterSpec(
            name="length", dimension=Dimension.LENGTH, minimum=q(1, "um"), maximum=q(1, "mm")
        ),
        ParameterSpec(name="width", dimension=Dimension.LENGTH, default=q(0.5, "um")),
    )
)


def codes_at(
    schema: DesignParameterSchema, **values: Quantity
) -> list[tuple[str, tuple[str | int, ...]]]:
    return [(d.code, d.location) for d in schema.check(DesignValues(values=values))]


def test_valid_values_have_no_diagnostics_and_defaults_fill_in() -> None:
    assert codes_at(SCHEMA, length=q(100, "um")) == []
    assert SCHEMA.with_defaults({"length": q(100, "um")})["width"] == q(0.5, "um")


def test_missing_parameter_without_default() -> None:
    assert codes_at(SCHEMA, width=q(1, "um")) == [("params.missing", ("values", "length"))]


def test_unknown_parameter() -> None:
    assert codes_at(SCHEMA, length=q(10, "um"), depth=q(1, "um")) == [
        ("params.unknown", ("values", "depth"))
    ]


def test_invalid_units() -> None:
    assert codes_at(SCHEMA, length=q(10, "furlong")) == [
        ("units.unknown_unit", ("values", "length"))
    ]
    assert codes_at(SCHEMA, length=q(10, "ps")) == [
        ("units.dimension_mismatch", ("values", "length"))
    ]


def test_bounds_are_inclusive_and_unit_aware() -> None:
    assert codes_at(SCHEMA, length=q(1000, "um")) == []
    assert codes_at(SCHEMA, length=q(1, "mm")) == []
    assert codes_at(SCHEMA, length=q(0.5, "um")) == [("params.out_of_bounds", ("values", "length"))]
    assert codes_at(SCHEMA, length=q(1.5, "mm")) == [("params.out_of_bounds", ("values", "length"))]


def test_all_problems_are_reported_together() -> None:
    assert len(codes_at(SCHEMA, width=q(1, "ps"), extra=q(1, "um"))) == 3


def test_diagnostics_carry_the_subject() -> None:
    [d] = SCHEMA.check(DesignValues(), subject="design:x")[:1]
    assert d.subject == "design:x"


def test_roles_cannot_be_mixed() -> None:
    operating = OperatingConditionSchema(
        parameters=(ParameterSpec(name="wavelength", dimension=Dimension.LENGTH),)
    )
    ok = OperatingValues(values={"wavelength": q(1550, "nm")})
    assert operating.check(ok) == []
    # Values of another role are a diagnostic, even when names and units would match.
    [d] = operating.check(DesignValues(values={"wavelength": q(1550, "nm")}))
    assert d.code == "params.role_mismatch"
    [d] = SCHEMA.check(FittedCoefficients())
    assert d.code == "params.role_mismatch"
    # A payload of one role does not validate as another role.
    with pytest.raises(ValidationError):
        DesignParameterSchema.model_validate(operating.model_dump())
    with pytest.raises(ValidationError):
        DesignValues.model_validate(ok.model_dump())


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "1bad", "dimension": Dimension.LENGTH},
        {"name": "w", "dimension": Dimension.LENGTH, "default": q(1, "ps")},
        {"name": "w", "dimension": Dimension.LENGTH, "minimum": q(2, "um"), "maximum": q(1, "um")},
        {"name": "w", "dimension": Dimension.LENGTH, "default": q(5, "um"), "maximum": q(1, "um")},
    ],
)
def test_inconsistent_specs_are_rejected(spec: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ParameterSpec(**spec)  # type: ignore[arg-type]


def test_duplicate_parameter_names_are_rejected() -> None:
    spec = ParameterSpec(name="w", dimension=Dimension.LENGTH)
    with pytest.raises(ValidationError, match="unique"):
        DesignParameterSchema(parameters=(spec, spec))
