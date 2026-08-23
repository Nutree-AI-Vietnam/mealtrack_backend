"""Locale-aware display-name lookups for the food-reference catalog.

``get_display_projections`` is id-keyed and skips the public-eligibility
filter: owned meal lines must keep painting a name for a row that was
verified at save time but later quarantined. Nutrition trust for those
same ids remains governed by ``get_nutrition_projection`` /
``get_nutrition_projections``, which do enforce eligibility.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.repositories.food_reference_integrity_repository import (
    FoodReferenceIntegrityRepository,
)
from src.infra.repositories.food_reference_projection import (
    food_reference_model_to_dict,
)

_DISPLAY_LOAD_OPTIONS = (
    selectinload(FoodReferenceModel.serving_size_rows),
    selectinload(FoodReferenceModel.nutrient_rows),
)


class FoodReferenceLocaleRepository:
    """Batch locale display-name matches and id-keyed display projections."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._integrity_repository = FoodReferenceIntegrityRepository(session)

    async def find_by_locale_names(
        self, language: str, names: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Exact (casefold) locale display-name match, eligible rows only."""
        by_casefold = {
            name.casefold(): name
            for name in names
            if isinstance(name, str) and name.strip()
        }
        if not by_casefold:
            return {}
        keys = list(by_casefold)
        conditions = [func.lower(FoodReferenceModel.name).in_(keys)]
        if language == "vi":
            conditions.append(func.lower(FoodReferenceModel.name_vi).in_(keys))
        stmt = (
            select(FoodReferenceModel)
            .where(self._integrity_repository.public_eligibility_clause())
            .where(or_(*conditions))
            .options(*_DISPLAY_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().unique().all()
        if not models:
            return {}
        matched: dict[str, dict[str, Any]] = {}
        for model in models:
            candidates = {str(model.name or "").casefold()}
            if language == "vi" and model.name_vi:
                candidates.add(model.name_vi.casefold())
            for candidate in candidates:
                original = by_casefold.get(candidate)
                if original and original not in matched:
                    matched[original] = food_reference_model_to_dict(model)
        return matched

    async def get_display_projections(
        self, food_reference_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """Id-keyed English name + ``name_vi`` for linked meal lines.

        No eligibility filter here: display projections are id-keyed for
        already-owned meal lines, unlike nutrition projections.
        """
        ids = sorted({int(value) for value in food_reference_ids})
        if not ids:
            return {}
        result = await self._session.execute(
            select(
                FoodReferenceModel.id,
                FoodReferenceModel.name,
                FoodReferenceModel.name_vi,
            ).where(FoodReferenceModel.id.in_(ids))
        )
        return {
            row.id: {
                "name": row.name,
                "name_vi": row.name_vi,
            }
            for row in result.all()
        }


__all__ = ["FoodReferenceLocaleRepository"]
