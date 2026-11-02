"""Product reads behind the one visibility rule: shared and approved, or your own."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.domain.model.supplement import (
    ReviewStatus,
    SupplementProduct,
    SupplementProductNotFoundError,
    gtin14,
)
from src.domain.utils.food_search_text import (
    MAX_FOOD_SEARCH_WORDS,
    clip_food_search_query,
    food_search_words,
    search_word_pattern,
)
from src.infra.database.models.supplement import (
    SupplementIntakeORM as Intake,
)
from src.infra.database.models.supplement import (
    SupplementProductBarcodeORM as Barcode,
)
from src.infra.database.models.supplement import (
    SupplementProductORM as Product,
)
from src.infra.database.models.supplement import (
    UserSupplementORM as ListEntry,
)
from src.infra.mappers.supplement_mapper import product_to_domain

logger = logging.getLogger(__name__)

_MAX_SEARCH_LIMIT = 50


def visible_to(user_id: str) -> ColumnElement[bool]:
    # An empty id would compare as "owner IS NULL" and expose unapproved shared rows.
    if not user_id:
        raise ValueError("a visibility check needs a user id")
    return or_(
        and_(
            Product.owner_user_id.is_(None),
            Product.review_status == ReviewStatus.APPROVED.value,
        ),
        Product.owner_user_id == user_id,
    )


class SupplementProductReads:
    """Every product read that takes an id, a barcode or a search term."""

    session: AsyncSession

    async def get_product(
        self, product_id: str, user_id: str
    ) -> SupplementProduct | None:
        rows = await self._products(
            select(Product).where(Product.id == product_id, visible_to(user_id))
        )
        return rows[0] if rows else None

    async def find_by_barcode(
        self, barcode: str, user_id: str
    ) -> SupplementProduct | None:
        code = gtin14(barcode)
        if code is None:
            return None
        rows = await self._products(
            select(Product)
            .join(Barcode, Barcode.product_id == Product.id)
            .where(Barcode.barcode == code, visible_to(user_id))
            .order_by(Product.owner_user_id.is_(None).desc())
            .limit(1)
        )
        return rows[0] if rows else None

    async def search_products(
        self, query: str, user_id: str, limit: int = 20
    ) -> list[SupplementProduct]:
        words = list(dict.fromkeys(food_search_words(clip_food_search_query(query))))
        words = words[:MAX_FOOD_SEARCH_WORDS]
        if not words:
            return []
        return await self._products(
            select(Product)
            .where(
                visible_to(user_id),
                *(
                    Product.name_search.like(search_word_pattern(word))
                    for word in words
                ),
            )
            .order_by(
                Product.owner_user_id.is_(None).desc(),
                Product.sold_in_vn.desc(),
                func.similarity(Product.name_search, " ".join(words)).desc(),
                Product.brand,
                Product.name,
            )
            .limit(max(1, min(limit, _MAX_SEARCH_LIMIT)))
        )

    async def products_for(
        self, user_id: str, product_ids: Iterable[str]
    ) -> dict[str, SupplementProduct]:
        ids = list(dict.fromkeys(product_ids))
        if not ids:
            return {}
        # The user's own doses and list rows keep their product readable even
        # if a shared row is later withdrawn from the catalog.
        referenced = or_(
            Product.id.in_(select(Intake.product_id).where(Intake.user_id == user_id)),
            Product.id.in_(
                select(ListEntry.product_id).where(ListEntry.user_id == user_id)
            ),
        )
        rows = await self._products(
            select(Product).where(
                Product.id.in_(ids), or_(visible_to(user_id), referenced)
            )
        )
        return {product.id: product for product in rows}

    async def _require_visible(self, product_id: str, user_id: str) -> None:
        # Another user's private product must never gain doses or list rows:
        # its owner's account deletion would then be blocked or lose their rows.
        found = await self.session.execute(
            select(Product.id).where(Product.id == product_id, visible_to(user_id))
        )
        if found.scalar_one_or_none() is None:
            raise SupplementProductNotFoundError(product_id)

    async def _products(self, statement: Any) -> list[SupplementProduct]:
        rows = (await self.session.execute(statement)).scalars().all()
        if not rows:
            return []
        codes: dict[str, list[str]] = {}
        for product_id, code in (
            await self.session.execute(
                select(Barcode.product_id, Barcode.barcode).where(
                    Barcode.product_id.in_([row.id for row in rows])
                )
            )
        ).all():
            codes.setdefault(product_id, []).append(code)
        products = []
        for row in rows:
            try:
                products.append(product_to_domain(row, codes.get(row.id, [])))
            except ValueError:
                # One malformed row must not break every search that returns it.
                logger.warning(
                    "Skipping invalid supplement product %s", row.id, exc_info=True
                )
        return products
