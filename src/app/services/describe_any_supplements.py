"""Which product a supplement in a Ghi nhanh sentence means.

The person's own list comes first: the model sees it as short refs and
answers with one. Then a branded product the sentence names is looked up in
what the person may see (approved catalog rows, then their own). Anything
else becomes their private estimated product at save time. A product that
carries calories (a protein powder in the catalog) is food, not a dose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.app.services.describe_any_items import DescribedSupplement
from src.domain.model.supplement import SupplementProduct

MAX_SHELF = 30
_SHELF_NAME_MAX_CHARS = 80
_UNSAFE = re.compile(r"[<>\r\n]+")


@dataclass(frozen=True)
class SupplementShelf:
    """The person's listed products, by the refs the model sees (s1, s2, ...)."""

    by_ref: dict[str, SupplementProduct] = field(default_factory=dict)

    def block(self) -> str:
        """The data block sent ahead of the sentence; empty when nothing is listed."""
        if not self.by_ref:
            return ""
        lines = "\n".join(
            f"- {ref}: {_label(product)}" for ref, product in self.by_ref.items()
        )
        return f"<my_supplements>\n{lines}\n</my_supplements>\n"


async def load_shelf(uow: Any, user_id: str) -> SupplementShelf:
    """Listed products the model may match, active ones first."""
    entries = sorted(
        await uow.supplements.list_supplements(user_id),
        key=lambda entry: not entry.active,
    )
    products = await uow.supplements.products_for(
        user_id, [entry.product_id for entry in entries]
    )
    # products_for keeps a withdrawn catalog row readable for history; a new
    # dose of it would be refused, so the model never sees it.
    listed = [
        product
        for entry in entries
        if (product := products.get(entry.product_id)) is not None
        and product.visible_to(user_id)
        and not product.macro_bearing
    ]
    return SupplementShelf(
        {f"s{n}": product for n, product in enumerate(listed[:MAX_SHELF], start=1)}
    )


async def find_product(
    uow: Any, user_id: str, item: DescribedSupplement, shelf: SupplementShelf
) -> SupplementProduct | None:
    """The listed or catalog product the item names; None means estimate it."""
    if item.list_ref and item.list_ref in shelf.by_ref:
        return shelf.by_ref[item.list_ref]
    if not item.catalog_query:
        return None
    found = await uow.supplements.search_products(item.catalog_query, user_id, 1)
    return found[0] if found else None


def _label(product: SupplementProduct) -> str:
    text = " ".join(part for part in (product.brand, product.name) if part)
    text = _UNSAFE.sub(" ", text)[:_SHELF_NAME_MAX_CHARS].strip()
    return f"{text} ({product.unit_label})"
