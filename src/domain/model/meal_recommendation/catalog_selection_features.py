"""Stable recipe constraints computed from the authoritative catalog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogSelectionFeatures:
    haystack: str
    contains_meat: bool
    contains_pork: bool
    non_meal: bool


@dataclass(frozen=True)
class CatalogPublicationVersion:
    """Independent dependency facets; enrichment cannot invalidate selection."""

    selection: int
    ingredients: int
    translation: int
    enrichment: int
