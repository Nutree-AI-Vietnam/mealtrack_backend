"""Async repository for curated catalog meal projections."""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import cast

from sqlalchemy import and_, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.services.weekly_meal_planner.recipe_publication import (
    projection_nutrition_quantity,
    publish_recipe,
)
from src.domain.model.meal_recommendation.catalog_recipe import (
    CatalogMeal,
    CatalogMealIngredient,
    CatalogMealStep,
)
from src.domain.ports.catalog_recipe_repository_port import (
    CatalogMealRepositoryPort,
    CatalogMealRevision,
    CatalogMealSeedExisting,
    CatalogMealSeedSignature,
    CatalogMealSeedWrite,
    CatalogPopularPage,
)
from src.domain.services.meal_recommendation.ingredient_quantity_conversion_service import (
    IngredientQuantityConversionService,
    ResolvedIngredientQuantity,
)
from src.infra.database.models.food_reference_alias import FoodReferenceAliasORM
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.meal_recommendation import (
    AllergenReferenceORM,
    MealCatalogAllergenORM,
    MealCatalogIngredientORM,
    MealCatalogORM,
    MealCatalogStepORM,
)
from src.infra.repositories.food_reference_projection import (
    food_reference_model_to_nutrition_projection,
)

_CATALOG_CONVERTER = IngredientQuantityConversionService(
    allow_unverified=True,
    allow_unapproved_sources=True,
    allow_implausible_macros=True,
    allow_common_unit_fallbacks=True,
)


class AsyncCatalogMealRepository(CatalogMealRepositoryPort):
    """Repository for active catalog meals."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_active_meals(
        self,
        *,
        cuisine: str | None = None,
        meal_type: str | None = None,
    ) -> list[CatalogMeal]:
        stmt = (
            select(MealCatalogORM)
            .where(MealCatalogORM.is_active.is_(True))
            .options(*_catalog_meal_load_options())
            .order_by(MealCatalogORM.id)
        )
        if cuisine is not None:
            stmt = stmt.where(MealCatalogORM.cuisine == cuisine)
        if meal_type is not None:
            column = _meal_type_column(meal_type)
            stmt = stmt.where(column.is_(True))

        result = await self._session.execute(stmt)
        return [_meal_to_domain(row) for row in result.scalars().unique().all()]

    async def list_popular_page(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
        cuisine: str | None = None,
        meal_type: str | None = None,
        shuffle_seed: str | None = None,
    ) -> CatalogPopularPage:
        match = _browse_match_clause(query=query, cuisine=cuisine, meal_type=meal_type)
        stats = await self._session.execute(
            select(
                func.coalesce(
                    func.bool_or(MealCatalogORM.popularity_rank.is_not(None)), False
                ),
                func.count().filter(match),
                func.count().filter(
                    and_(match, MealCatalogORM.popularity_rank.is_(None))
                ),
            ).where(MealCatalogORM.is_active.is_(True))
        )
        any_ranked, total, unranked_count = stats.one()
        if not any_ranked or int(unranked_count or 0) > 0:
            return CatalogPopularPage(
                items=(),
                total=int(total or 0),
                any_ranked=bool(any_ranked),
                unranked_count=int(unranked_count or 0),
            )

        result = await self._session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.is_active.is_(True))
            .where(match)
            .options(*_catalog_meal_load_options())
            .order_by(*_popular_order(shuffle_seed))
            .offset(offset)
            .limit(limit)
        )
        return CatalogPopularPage(
            items=tuple(
                _meal_to_domain(row) for row in result.scalars().unique().all()
            ),
            total=int(total or 0),
            any_ranked=True,
            unranked_count=0,
        )

    async def get_active_catalog_revision(self) -> CatalogMealRevision:
        result = await self._session.execute(
            select(
                func.count(func.distinct(MealCatalogORM.id)),
                func.max(MealCatalogORM.updated_at),
                func.max(FoodReferenceModel.updated_at),
            )
            .select_from(MealCatalogORM)
            .outerjoin(MealCatalogIngredientORM)
            .outerjoin(FoodReferenceModel)
            .where(MealCatalogORM.is_active.is_(True))
        )
        active_count, catalog_updated_at, food_reference_updated_at = result.one()
        return CatalogMealRevision(
            active_count=int(active_count or 0),
            catalog_updated_at=catalog_updated_at,
            food_reference_updated_at=food_reference_updated_at,
        )

    async def get_meal(self, catalog_meal_id: str) -> CatalogMeal | None:
        result = await self._session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.id == catalog_meal_id)
            .where(MealCatalogORM.is_active.is_(True))
            .options(*_catalog_meal_load_options())
        )
        row = result.scalar_one_or_none()
        return _meal_to_domain(row) if row else None

    async def get_meal_detail(self, catalog_meal_id: str) -> CatalogMeal | None:
        result = await self._session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.id == catalog_meal_id)
            .where(MealCatalogORM.is_active.is_(True))
            .options(*_catalog_meal_detail_load_options())
        )
        row = result.scalar_one_or_none()
        return _meal_to_domain(row, include_steps=True) if row else None

    async def get_active_release(self):
        """Temporary compatibility: the four-table catalog has no release row."""

        return None

    async def find_seed_existing(
        self,
        *,
        catalog_key: str,
        content_hash: str,
    ) -> CatalogMealSeedExisting | None:
        result = await self._session.execute(
            select(MealCatalogORM.catalog_key, MealCatalogORM.content_hash).where(
                or_(
                    MealCatalogORM.catalog_key == catalog_key,
                    MealCatalogORM.content_hash == content_hash,
                )
            )
        )
        row = result.first()
        if row is None:
            return None
        return CatalogMealSeedExisting(
            catalog_key=cast(str, row.catalog_key),
            content_hash=cast(str, row.content_hash),
        )

    async def add_seed_meal(self, seed: CatalogMealSeedWrite) -> None:
        alias_rows = await self._food_alias_pairs()
        allergen_rows = await self._allergen_reference_rows()
        published = publish_recipe(
            recipe_name=seed.name,
            description=seed.description,
            ingredients=[
                {
                    "name": item.display_name,
                    "food_reference_id": item.food_reference_id,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "category": item.category,
                }
                for item in seed.ingredients
            ],
            instructions=[
                {"step": step_number, "title": title, "instruction": description}
                for step_number, title, description in seed.steps
            ],
            nutrition_ready=True,
            publish=True,
            equipment=_text_list(seed.equipment),
            allergen_disclosures=_text_list(seed.allergens),
            source={"publisher": seed.source_name, "url": seed.source_url},
            aliases=alias_rows,
            known_allergen_codes=[row.code for row in allergen_rows],
        )
        row = MealCatalogORM(
            catalog_key=seed.catalog_key,
            content_hash=seed.content_hash,
            name=seed.name,
            cuisine=seed.cuisine,
            description=seed.description,
            image_url=seed.image_url,
            popularity_rank=seed.popularity_rank,
            breakfast_eligible="breakfast" in seed.meal_types,
            lunch_eligible="lunch" in seed.meal_types,
            dinner_eligible="dinner" in seed.meal_types,
            snack_eligible="snack" in seed.meal_types,
            is_active=True,
            recipe_payload=published.recipe_payload,
            payload_schema_version=published.payload_schema_version,
            payload_digest=published.payload_digest,
            publication_status=published.publication_status,
            nutrition_status=published.nutrition_status,
        )
        row.ingredients = [
            MealCatalogIngredientORM(
                food_reference_id=item.food_reference_id,
                position=item.position,
                display_name=item.display_name,
                quantity=item.quantity,
                unit=item.unit,
                category=item.category,
                raw_text=item.raw_text,
                quantity_text=item.quantity_text,
                is_optional=item.is_optional,
            )
            for item in published.ingredients
        ]
        row.steps = [
            MealCatalogStepORM(
                step_number=step.step_number,
                title=step.title,
                description=step.description,
            )
            for step in published.steps
        ]
        allergens_by_code = {row.code: row for row in allergen_rows}
        row.allergen_links = [
            MealCatalogAllergenORM(
                allergen=allergens_by_code[code],
                source="explicit",
            )
            for code in published.allergen_codes
            if code in allergens_by_code
        ]
        row.source_name = seed.source_name
        row.source_url = seed.source_url
        row.prep_time_minutes = seed.prep_time_minutes
        row.cook_time_minutes = seed.cook_time_minutes
        row.tag = seed.tag
        row.allergens = seed.allergens
        row.summary = seed.summary
        row.equipment = seed.equipment
        row.base_servings = seed.base_servings
        row.serving_source = seed.serving_source
        row.serving_confidence = seed.serving_confidence
        self._session.add(row)
        await self._session.flush()

    async def _food_alias_pairs(self) -> list[tuple[str, int]]:
        result = await self._session.execute(
            select(FoodReferenceAliasORM.alias, FoodReferenceAliasORM.food_reference_id)
        )
        return [(str(alias), int(food_id)) for alias, food_id in result.all()]

    async def _allergen_reference_rows(self) -> list[AllergenReferenceORM]:
        result = await self._session.execute(select(AllergenReferenceORM))
        return list(result.scalars().all())

    async def update_popularity_rank(
        self, *, catalog_key: str, popularity_rank: int | None
    ) -> None:
        await self._session.execute(
            update(MealCatalogORM)
            .where(MealCatalogORM.catalog_key == catalog_key)
            .values(popularity_rank=popularity_rank)
        )
        await self._session.flush()

    async def lock_seed_import(self) -> None:
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtext("meal_catalog_seed_import"))
            )
        )

    async def list_seed_signatures(self) -> list[CatalogMealSeedSignature]:
        result = await self._session.execute(
            select(MealCatalogORM)
            .options(selectinload(MealCatalogORM.ingredients))
            .order_by(MealCatalogORM.id)
        )
        return [
            CatalogMealSeedSignature(
                catalog_key=cast(str, row.catalog_key),
                content_hash=cast(str, row.content_hash),
                normalized_name=_normalize_catalog_text(cast(str, row.name)),
                normalized_cuisine=_normalize_catalog_text(cast(str, row.cuisine)),
                food_reference_ids=frozenset(
                    cast(int, ingredient.food_reference_id)
                    for ingredient in row.ingredients
                ),
            )
            for row in result.scalars().unique().all()
        ]


def _browse_match_clause(
    *, query: str | None, cuisine: str | None, meal_type: str | None
):
    clauses = []
    if cuisine is not None:
        clauses.append(func.lower(MealCatalogORM.cuisine) == cuisine.strip().casefold())
    if meal_type is not None:
        clauses.append(_meal_type_column(meal_type).is_(True))
    needle = (query or "").strip().casefold()
    if needle:
        pattern = f"%{_escape_like(needle)}%"
        clauses.append(
            or_(
                func.lower(MealCatalogORM.name).like(pattern, escape="\\"),
                func.lower(MealCatalogORM.cuisine).like(pattern, escape="\\"),
            )
        )
    return and_(*clauses) if clauses else true()


def _popular_order(shuffle_seed: str | None):
    if shuffle_seed:
        return (
            func.md5(func.concat(MealCatalogORM.id, ":", shuffle_seed)).asc(),
            MealCatalogORM.id.asc(),
        )
    return (
        MealCatalogORM.popularity_rank.asc(),
        func.lower(MealCatalogORM.name).asc(),
        MealCatalogORM.id.asc(),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _meal_type_column(meal_type: str):
    if meal_type == "breakfast":
        return MealCatalogORM.breakfast_eligible
    if meal_type == "lunch":
        return MealCatalogORM.lunch_eligible
    if meal_type == "dinner":
        return MealCatalogORM.dinner_eligible
    if meal_type == "snack":
        return MealCatalogORM.snack_eligible
    raise ValueError(f"unsupported meal_type: {meal_type}")


def _catalog_meal_load_options():
    return (
        selectinload(MealCatalogORM.ingredients)
        .selectinload(MealCatalogIngredientORM.food_reference)
        .selectinload(FoodReferenceModel.serving_size_rows),
        selectinload(MealCatalogORM.allergen_links).selectinload(
            MealCatalogAllergenORM.allergen
        ),
    )


def _catalog_meal_detail_load_options():
    return (*_catalog_meal_load_options(), selectinload(MealCatalogORM.steps))


def _meal_to_domain(row: MealCatalogORM, *, include_steps: bool = False) -> CatalogMeal:
    nutrition = _nutrition_totals(row)
    return CatalogMeal(
        id=cast(str, row.id),
        catalog_key=cast(str, row.catalog_key),
        content_hash=cast(str, row.content_hash),
        name=cast(str, row.name),
        cuisine=cast(str, row.cuisine),
        description=cast(str | None, row.description),
        image_url=cast(str | None, row.image_url),
        popularity_rank=_optional_int(getattr(row, "popularity_rank", None)),
        protein_g=_decimal(nutrition.protein),
        carbs_g=_decimal(nutrition.carbs),
        fat_g=_decimal(nutrition.fat),
        fiber_g=_decimal(nutrition.fiber),
        sugar_g=_decimal(nutrition.sugar),
        meal_types=_meal_types(row),
        ingredients=tuple(_ingredient_to_domain(item) for item in row.ingredients),
        is_active=cast(bool, row.is_active),
        source_name=cast(str | None, getattr(row, "source_name", None)),
        source_url=cast(str | None, getattr(row, "source_url", None)),
        prep_time_minutes=_optional_int(getattr(row, "prep_time_minutes", None)),
        cook_time_minutes=_optional_int(getattr(row, "cook_time_minutes", None)),
        tag=cast(str | None, getattr(row, "tag", None)),
        allergens=cast(str | None, getattr(row, "allergens", None)),
        summary=cast(str | None, getattr(row, "summary", None)),
        equipment=cast(str | None, getattr(row, "equipment", None)),
        base_servings=_optional_int(getattr(row, "base_servings", None)),
        serving_source=cast(str | None, getattr(row, "serving_source", None)),
        serving_confidence=cast(str, getattr(row, "serving_confidence", "unknown")),
        steps=(
            tuple(_step_to_domain(step) for step in row.steps) if include_steps else ()
        ),
        publication_status=cast(str, getattr(row, "publication_status", "published")),
        nutrition_status=cast(str, getattr(row, "nutrition_status", "ready")),
        allergen_codes=_allergen_codes(row),
        recipe_payload=getattr(row, "recipe_payload", None),
    )


def _nutrition_totals(row: MealCatalogORM) -> ResolvedIngredientQuantity:
    totals = {
        "protein": 0.0,
        "carbs": 0.0,
        "fat": 0.0,
        "fiber": 0.0,
        "sugar": 0.0,
        "calories": 0.0,
    }
    for ingredient in row.ingredients:
        if (
            projection_nutrition_quantity(
                {
                    "food_reference_id": ingredient.food_reference_id,
                    "quantity": ingredient.quantity,
                    "quantity_text": getattr(ingredient, "quantity_text", None),
                }
            )
            == 0
        ):
            continue
        resolved = _resolve_ingredient_nutrition(ingredient)
        totals["protein"] += resolved.protein
        totals["carbs"] += resolved.carbs
        totals["fat"] += resolved.fat
        totals["fiber"] += resolved.fiber
        totals["sugar"] += resolved.sugar
        totals["calories"] += resolved.calories
    return ResolvedIngredientQuantity(
        food_reference_id=None,
        display_name=cast(str, row.name),
        quantity=1,
        unit="meal",
        grams=0,
        protein=totals["protein"],
        carbs=totals["carbs"],
        fat=totals["fat"],
        fiber=totals["fiber"],
        sugar=totals["sugar"],
        calories=totals["calories"],
    )


def _resolve_ingredient_nutrition(
    row: MealCatalogIngredientORM,
) -> ResolvedIngredientQuantity:
    reference = food_reference_model_to_nutrition_projection(row.food_reference)
    return _CATALOG_CONVERTER.resolve(
        reference=reference,
        quantity=float(row.quantity),
        unit=cast(str, row.unit),
        display_name=cast(str, row.display_name),
    )


def _ingredient_to_domain(row: MealCatalogIngredientORM) -> CatalogMealIngredient:
    return CatalogMealIngredient(
        food_reference_id=cast(int, row.food_reference_id),
        display_name=cast(str, row.display_name),
        quantity=_decimal(row.quantity),
        unit=cast(str, row.unit),
        category=cast(str, getattr(row, "category", "pantry")),
        position=_optional_int(getattr(row, "position", None)),
    )


def _step_to_domain(row: MealCatalogStepORM) -> CatalogMealStep:
    return CatalogMealStep(
        step_number=int(row.step_number),
        title=cast(str, row.title),
        description=cast(str, row.description),
    )


def _meal_types(row: MealCatalogORM) -> tuple[str, ...]:
    values: list[str] = []
    if row.breakfast_eligible:
        values.append("breakfast")
    if row.lunch_eligible:
        values.append("lunch")
    if row.dinner_eligible:
        values.append("dinner")
    if row.snack_eligible:
        values.append("snack")
    return tuple(values)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or "0"))


def _allergen_codes(row: MealCatalogORM) -> tuple[str, ...]:
    links = getattr(row, "allergen_links", ()) or ()
    codes = []
    for link in links:
        allergen = getattr(link, "allergen", None)
        code = getattr(allergen, "code", None)
        if code:
            codes.append(str(code))
    return tuple(codes)


def _text_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_catalog_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
