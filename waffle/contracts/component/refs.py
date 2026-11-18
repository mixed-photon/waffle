"""Typed revision references: a `RevisionRef` restricted to one entity kind."""

from __future__ import annotations

from typing import Annotated

from waffle.contracts.foundation.records import RevisionRefField, ref_to

InterfaceRef = Annotated[RevisionRefField, ref_to("interface")]
ComponentRef = Annotated[RevisionRefField, ref_to("component")]
ImplementationRef = Annotated[RevisionRefField, ref_to("implementation")]
DesignRef = Annotated[RevisionRefField, ref_to("design")]
TechnologyRef = Annotated[RevisionRefField, ref_to("technology")]
