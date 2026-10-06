"""Bounded catalog reads with complete legacy fallback during reconciliation."""

from dataclasses import replace

from sqlalchemy import exists, func, or_, select

from src.domain.model.weekly_meal_planner import SLOT_MEAL_TYPES
from src.domain.ports.catalog_recipe_repository_port import CatalogRecipePage
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    resolve_allergen_preferences,
)
from src.domain.services.weekly_meal_planner.meal_practicality import practicality_rank
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)
from src.infra.repositories.catalog_projection_builder import PROJECTION_SCHEMA_VERSION
from src.infra.repositories.catalog_projection_filters import (
    recipe_filters,
    recipe_order,
)
from src.infra.repositories.catalog_projection_mapper import projection_to_meal


class CatalogProjectionRepository:
    def __init__(self, repository):
        self.repository = repository
        self.session = repository._session

    @staticmethod
    def _base():
        # Live activation check makes withdrawals immediate even before rebuilding.
        return (
            select(Projection)
            .join(Source, Source.id == Projection.catalog_meal_id)
            .where(Source.is_active.is_(True))
        )

    async def complete(self, *, selection=False):
        dirty = [
            Projection.catalog_meal_id.is_(None),
            Projection.query_dirty.is_(True),
            Projection.schema_version != PROJECTION_SCHEMA_VERSION,
        ]
        if selection:
            dirty.append(Projection.nutrition_dirty.is_(True))
        invalid = (
            select(Source.id)
            .outerjoin(Projection, Source.id == Projection.catalog_meal_id)
            .where(Source.is_active.is_(True), or_(*dirty))
        )
        return not bool(
            (
                await self.repository._execute_catalog(select(exists(invalid)))
            ).scalar_one()
        )

    async def summaries(self, ids):
        ids = tuple(dict.fromkeys(identity for identity in ids if identity))
        if not ids:
            return []
        rows = (
            (
                await self.repository._execute_catalog(
                    self._base()
                    .where(Projection.catalog_meal_id.in_(ids))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        meals = {
            row.catalog_meal_id: projection_to_meal(row)
            for row in rows
            if not row.query_dirty
            and not row.nutrition_dirty
            and row.schema_version == PROJECTION_SCHEMA_VERSION
        }
        missing = [identity for identity in ids if identity not in meals]
        if missing:
            fetched = await self.repository.get_meals(missing)
            meals.update(
                {
                    meal.id: replace(
                        meal, ingredients=(), steps=(), recipe_payload=None
                    )
                    for meal in fetched
                }
            )
        return [meals[identity] for identity in ids if identity in meals]

    async def candidates(self):
        await self.repository.lock_catalog_publication(shared=True)
        if not await self.complete(selection=True):
            return await self.repository.list_active_meals()
        rows = (
            (
                await self.repository._execute_catalog(
                    self._base()
                    .order_by(Projection.catalog_meal_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return [projection_to_meal(row, candidate=True) for row in rows]

    async def page(
        self,
        *,
        query=None,
        diet=None,
        max_cook_time=None,
        cuisine=None,
        meal_type=None,
        dislikes=(),
        allergies=(),
        limit=20,
        offset=0,
    ):
        await self.repository.lock_catalog_publication(shared=True)
        known = await self.repository.list_allergen_codes() if allergies else ()
        codes = resolve_allergen_preferences(allergies, known)
        if codes is None:
            return CatalogRecipePage((), 0, projected=True)
        if not await self.complete():
            return await self._legacy_page(
                query=query,
                diet=diet,
                max_cook_time=max_cook_time,
                cuisine=cuisine,
                meal_type=meal_type,
                dislikes=dislikes,
                codes=codes,
                limit=limit,
                offset=offset,
            )
        filters = recipe_filters(
            query=query,
            diet=diet,
            max_cook_time=max_cook_time,
            cuisine=cuisine,
            meal_type=meal_type,
            dislikes=dislikes,
            allergen_codes=codes,
        )
        base = self._base().where(*filters)
        if meal_type in SLOT_MEAL_TYPES:
            return await self._practical_page(
                base, meal_type=meal_type, limit=limit, offset=offset
            )
        total = (
            await self.repository._execute_catalog(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        rows = (
            (
                await self.repository._execute_catalog(
                    base.order_by(
                        *recipe_order(
                            postgres=self.session.get_bind().dialect.name
                            == "postgresql"
                        )
                    )
                    .limit(limit)
                    .offset(offset)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        meals = {
            row.catalog_meal_id: projection_to_meal(row)
            for row in rows
            if not row.nutrition_dirty
        }
        dirty = [row.catalog_meal_id for row in rows if row.nutrition_dirty]
        if dirty:
            # Hydrate after count/page; dirty nutrition never removes matches or changes totals.
            meals.update(
                {
                    meal.id: replace(
                        meal, ingredients=(), steps=(), recipe_payload=None
                    )
                    for meal in await self.repository.get_meals(dirty)
                }
            )
        return CatalogRecipePage(
            tuple(
                meals[row.catalog_meal_id]
                for row in rows
                if row.catalog_meal_id in meals
            ),
            total,
            projected=True,
        )

    async def _practical_page(self, base, *, meal_type: str, limit: int, offset: int):
        """Page meal-time lists with everyday dishes ahead of fussy ones."""
        scored = (
            await self.repository._execute_catalog(
                base.with_only_columns(
                    Projection.catalog_meal_id,
                    Projection.name_casefold,
                    Projection.total_minutes,
                    Projection.popularity_rank,
                    Projection.non_meal,
                )
            )
        ).all()
        ranked = sorted(
            scored,
            key=lambda row: (
                practicality_rank(
                    row.name_casefold,
                    meal_type,
                    total_minutes=int(row.total_minutes or 0),
                    non_meal=bool(row.non_meal),
                ),
                row.popularity_rank is None,
                row.popularity_rank if row.popularity_rank is not None else 0,
                row.name_casefold,
                row.catalog_meal_id,
            ),
        )
        page_ids = [row.catalog_meal_id for row in ranked[offset : offset + limit]]
        if not page_ids:
            return CatalogRecipePage((), len(ranked), projected=True)
        rows = (
            (
                await self.repository._execute_catalog(
                    self._base()
                    .where(Projection.catalog_meal_id.in_(page_ids))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        meals = {
            row.catalog_meal_id: projection_to_meal(row)
            for row in rows
            if not row.nutrition_dirty
        }
        dirty = [row.catalog_meal_id for row in rows if row.nutrition_dirty]
        if dirty:
            meals.update(
                {
                    meal.id: replace(
                        meal, ingredients=(), steps=(), recipe_payload=None
                    )
                    for meal in await self.repository.get_meals(dirty)
                }
            )
        return CatalogRecipePage(
            tuple(meals[meal_id] for meal_id in page_ids if meal_id in meals),
            len(ranked),
            projected=True,
        )

    async def _legacy_page(self, **kwargs):
        from src.infra.repositories.catalog_projection_legacy_page import (
            legacy_recipe_page,
        )

        return await legacy_recipe_page(self.repository, **kwargs)
