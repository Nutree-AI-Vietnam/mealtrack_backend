"""Reads of a person's supplement list, catalog search and barcode lookup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.supplement import (
    GetMySupplementsQuery,
    LookupSupplementBarcodeQuery,
    SearchSupplementsQuery,
)
from src.app.services.supplement_payloads import (
    intake_payload,
    list_entry_payload,
    product_payload,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)


class _SupplementRead:
    def __init__(self, uow_factory: Callable[[], Any]):
        self.uow_factory = uow_factory


@handles(GetMySupplementsQuery)
class GetMySupplementsQueryHandler(
    _SupplementRead, EventHandler[GetMySupplementsQuery, dict]
):
    async def handle(self, query: GetMySupplementsQuery) -> dict:
        async with self.uow_factory() as uow:
            repo = uow.supplements
            day = query.target_date
            if day is None:
                user_tz = await resolve_user_timezone_async(
                    query.user_id, uow, query.header_timezone
                )
                day = utc_now().astimezone(get_zone_info(user_tz)).date()
            entries = await repo.list_supplements(query.user_id)
            doses = await repo.intakes_between(query.user_id, day, day)
            products = await repo.products_for(
                query.user_id,
                [e.product_id for e in entries] + [d.product_id for d in doses],
            )
        listed = {entry.product_id for entry in entries}
        return {
            "date": day.isoformat(),
            "items": [
                list_entry_payload(
                    entry,
                    products[entry.product_id],
                    query.user_id,
                    [d for d in doses if d.product_id == entry.product_id],
                )
                for entry in entries
                if entry.product_id in products
            ],
            # Doses of products since taken off the list still count for the day.
            "unlisted_doses": [
                {
                    **intake_payload(dose),
                    "product": product_payload(
                        products[dose.product_id], query.user_id
                    ),
                }
                for dose in doses
                if dose.product_id not in listed and dose.product_id in products
            ],
        }


@handles(SearchSupplementsQuery)
class SearchSupplementsQueryHandler(
    _SupplementRead, EventHandler[SearchSupplementsQuery, dict]
):
    async def handle(self, query: SearchSupplementsQuery) -> dict:
        async with self.uow_factory() as uow:
            found = await uow.supplements.search_products(query.query, query.user_id)
        return {"items": [product_payload(p, query.user_id) for p in found]}


@handles(LookupSupplementBarcodeQuery)
class LookupSupplementBarcodeQueryHandler(
    _SupplementRead, EventHandler[LookupSupplementBarcodeQuery, dict]
):
    async def handle(self, query: LookupSupplementBarcodeQuery) -> dict:
        async with self.uow_factory() as uow:
            product = await uow.supplements.find_by_barcode(
                query.barcode, query.user_id
            )
        if product is None:
            # The app then offers a label scan.
            raise ResourceNotFoundException(
                "No supplement with this barcode", "SUPPLEMENT_NOT_FOUND"
            )
        return product_payload(product, query.user_id)
