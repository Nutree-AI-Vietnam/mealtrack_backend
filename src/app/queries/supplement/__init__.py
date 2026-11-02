"""Supplement queries: the person's list, catalog search and barcode lookup."""

from dataclasses import dataclass
from datetime import date

from src.app.events.base import Query


@dataclass
class GetMySupplementsQuery(Query):
    """The list with the doses of ``target_date`` (the user's today when unset)."""

    user_id: str
    target_date: date | None = None
    header_timezone: str | None = None


@dataclass
class SearchSupplementsQuery(Query):
    user_id: str
    query: str


@dataclass
class LookupSupplementBarcodeQuery(Query):
    user_id: str
    barcode: str


__all__ = [
    "GetMySupplementsQuery",
    "LookupSupplementBarcodeQuery",
    "SearchSupplementsQuery",
]
