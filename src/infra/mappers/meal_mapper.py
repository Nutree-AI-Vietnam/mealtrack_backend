"""Meal cluster ORM <-> domain mapping functions."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from src.domain.model.meal.meal import Meal as DomainMeal
from src.domain.model.meal.meal_image import MealImage as DomainMealImage
from src.domain.model.meal.meal_translation_domain_models import (
    FoodItemTranslation as DomainFoodItemTranslation,
)
from src.domain.model.meal.meal_translation_domain_models import (
    MealTranslation as DomainMealTranslation,
)
from src.domain.model.nutrition import (
    FoodItem as DomainFoodItem,
)
from src.domain.model.nutrition import (
    Macros,
    NutritionOverride,
)
from src.domain.model.nutrition import (
    Nutrition as DomainNutrition,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.meal.food_item_translation_model import (
    FoodItemTranslationORM,
)
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.meal.meal_image import MealImageORM
from src.infra.database.models.meal.meal_instruction_step import MealInstructionStepORM
from src.infra.database.models.meal.meal_translation_model import MealTranslationORM
from src.infra.database.models.nutrition.food_item import FoodItemORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.mappers.status_mapper import MealStatusMapper

logger = logging.getLogger(__name__)

_UNHYDRATABLE_READY_MEAL_ERRORS = {
    "Meal with READY status must have nutrition data",
    "Meal with READY status must have ready_at timestamp",
}


def _nutrition_override_from_orm(value: dict | None) -> NutritionOverride | None:
    if not value or not isinstance(value, dict):
        return None
    try:
        return NutritionOverride(
            calories=float(value["calories"]),
            protein=float(value["protein"]),
            carbs=float(value["carbs"]),
            fat=float(value["fat"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Convert aware datetime to naive UTC for TIMESTAMP WITHOUT TIME ZONE columns."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# ORM -> Domain
# ---------------------------------------------------------------------------


def food_item_orm_to_domain(orm: FoodItemORM) -> DomainFoodItem:
    return DomainFoodItem(
        id=orm.id,
        name=orm.name,
        quantity=orm.quantity,
        unit=orm.unit,
        macros=Macros(
            protein=orm.protein,
            carbs=orm.carbs,
            fat=orm.fat,
            fiber=orm.fiber or 0.0,
            sugar=orm.sugar or 0.0,
        ),
        micros=None,
        confidence=orm.confidence,
        fdc_id=orm.fdc_id,
        food_reference_id=orm.food_reference_id,
        is_custom=orm.is_custom,
        allowed_units=orm.allowed_units,
        nutrition_override=_nutrition_override_from_orm(orm.nutrition_override),
        source_kind=getattr(orm, "source_kind", None),
        source_food_id=getattr(orm, "source_food_id", None),
        nutrition_contract_version=getattr(orm, "nutrition_contract_version", None),
        source_snapshot=getattr(orm, "source_snapshot", None),
    )


def nutrition_orm_to_domain(orm: NutritionORM) -> DomainNutrition:
    food_items = (
        [food_item_orm_to_domain(fi) for fi in orm.food_items]
        if orm.food_items
        else None
    )
    return DomainNutrition(
        macros=Macros(
            protein=orm.protein,
            carbs=orm.carbs,
            fat=orm.fat,
            fiber=orm.fiber or 0.0,
            sugar=orm.sugar or 0.0,
        ),
        micros=None,
        food_items=food_items,
        confidence_score=orm.confidence_score,
        nutrition_override=_nutrition_override_from_orm(orm.nutrition_override),
    )


def meal_image_orm_to_domain(orm: MealImageORM) -> DomainMealImage:
    return DomainMealImage(
        image_id=orm.image_id,
        format=orm.format,
        size_bytes=orm.size_bytes,
        width=orm.width,
        height=orm.height,
        url=orm.url,
    )


def food_item_translation_orm_to_domain(
    orm: FoodItemTranslationORM,
) -> DomainFoodItemTranslation:
    return DomainFoodItemTranslation(
        food_item_id=orm.food_item_id,
        name=orm.name,
        description=orm.description,
    )


def meal_translation_orm_to_domain(orm: MealTranslationORM) -> DomainMealTranslation:
    return DomainMealTranslation(
        meal_id=orm.meal_id,
        language=orm.language,
        dish_name=orm.dish_name,
        food_items=[food_item_translation_orm_to_domain(fi) for fi in orm.food_items],
        translated_at=orm.translated_at,
        meal_instruction=orm.meal_instruction,
        meal_ingredients=orm.meal_ingredients,
        translation_version=getattr(orm, "translation_version", None),
    )


def _instructions_from_rows(orm: MealORM) -> list | None:
    rows = getattr(orm, "instruction_steps", None)
    if not rows:
        return orm.instructions
    return [
        {
            "instruction": row.instruction,
            "duration_minutes": row.duration_minutes,
        }
        for row in rows
    ]


def meal_orm_to_domain(orm: MealORM) -> DomainMeal:
    translations_dict: dict[str, DomainMealTranslation] | None = None
    if orm.translations:
        translations_dict = {
            t.language: meal_translation_orm_to_domain(t) for t in orm.translations
        }

    return DomainMeal(
        meal_id=orm.meal_id,
        user_id=orm.user_id,
        status=MealStatusMapper.to_domain(orm.status),
        created_at=orm.created_at,
        image=meal_image_orm_to_domain(orm.image) if orm.image else None,
        dish_name=orm.dish_name,
        meal_type=orm.meal_type,
        nutrition=nutrition_orm_to_domain(orm.nutrition) if orm.nutrition else None,
        ready_at=orm.ready_at,
        error_message=orm.error_message,
        raw_gpt_json=orm.raw_ai_response,
        food_label_metadata=orm.food_label_metadata,
        updated_at=orm.updated_at,
        last_edited_at=orm.last_edited_at,
        edit_count=orm.edit_count,
        is_manually_edited=orm.is_manually_edited,
        translations=translations_dict,
        source=orm.source,
        catalog_meal_id=getattr(orm, "catalog_meal_id", None),
        description=orm.description,
        instructions=_instructions_from_rows(orm),
        prep_time_min=orm.prep_time_min,
        cook_time_min=orm.cook_time_min,
        cuisine_type=orm.cuisine_type,
        origin_country=orm.origin_country,
        emoji=orm.emoji,
        quantity=orm.quantity,
    )


def meal_orm_to_domain_if_hydratable(orm: MealORM) -> DomainMeal | None:
    """Map a meal row, returning None for known malformed READY legacy rows."""
    try:
        return meal_orm_to_domain(orm)
    except ValueError as exc:
        if str(exc) in _UNHYDRATABLE_READY_MEAL_ERRORS:
            logger.warning(
                "Skipping unhydratable READY meal row: meal_id=%s reason=%s",
                getattr(orm, "meal_id", None),
                str(exc),
            )
            return None
        raise


# ---------------------------------------------------------------------------
# Domain -> ORM
# ---------------------------------------------------------------------------


def food_item_domain_to_orm(domain: DomainFoodItem, nutrition_id=None) -> FoodItemORM:
    item = FoodItemORM(
        name=domain.name,
        quantity=domain.quantity,
        unit=domain.unit,
        confidence=domain.confidence,
        nutrition_id=nutrition_id,
        fdc_id=getattr(domain, "fdc_id", None),
        food_reference_id=getattr(domain, "food_reference_id", None),
        is_custom=getattr(domain, "is_custom", False),
        allowed_units=getattr(domain, "allowed_units", None),
        nutrition_override=(
            domain.nutrition_override.to_dict() if domain.nutrition_override else None
        ),
        source_kind=getattr(domain, "source_kind", None),
        source_food_id=getattr(domain, "source_food_id", None),
        nutrition_contract_version=getattr(domain, "nutrition_contract_version", None),
        source_snapshot=getattr(domain, "source_snapshot", None),
    )
    if hasattr(domain, "id") and domain.id:
        item.id = str(domain.id)
    if domain.macros:
        item.protein = domain.macros.protein
        item.carbs = domain.macros.carbs
        item.fat = domain.macros.fat
        item.fiber = domain.macros.fiber
        item.sugar = domain.macros.sugar
    return item


def nutrition_domain_to_orm(domain: DomainNutrition, meal_id: str) -> NutritionORM:
    meal_id_str = str(meal_id) if meal_id else None
    orm = NutritionORM(confidence_score=domain.confidence_score, meal_id=meal_id_str)
    orm.nutrition_override = (
        domain.nutrition_override.to_dict() if domain.nutrition_override else None
    )
    if domain.macros:
        orm.protein = domain.macros.protein
        orm.carbs = domain.macros.carbs
        orm.fat = domain.macros.fat
        orm.fiber = domain.macros.fiber
        orm.sugar = domain.macros.sugar
    if domain.food_items:
        orm.food_items = [food_item_domain_to_orm(fi) for fi in domain.food_items]
        for idx, fi_orm in enumerate(orm.food_items):
            fi_orm.order_index = idx
    return orm


def meal_image_domain_to_orm(domain: DomainMealImage) -> MealImageORM:
    return MealImageORM(
        image_id=str(domain.image_id),
        url=domain.url,
        format=domain.format,
        size_bytes=domain.size_bytes,
        width=domain.width,
        height=domain.height,
    )


def food_item_translation_domain_to_orm(
    domain: DomainFoodItemTranslation, meal_translation_id: int
) -> FoodItemTranslationORM:
    return FoodItemTranslationORM(
        meal_translation_id=meal_translation_id,
        food_item_id=str(domain.food_item_id),
        name=domain.name,
        description=domain.description,
    )


def meal_translation_domain_to_orm(domain: DomainMealTranslation) -> MealTranslationORM:
    now = utc_now()
    translation = MealTranslationORM(
        meal_id=domain.meal_id,
        language=domain.language,
        dish_name=domain.dish_name,
        translated_at=_to_naive_utc(domain.translated_at or now),
        created_at=_to_naive_utc(now),
        meal_instruction=domain.meal_instruction,
        meal_ingredients=domain.meal_ingredients,
        translation_version=domain.translation_version,
    )
    for fi in domain.food_items:
        translation.food_items.append(
            FoodItemTranslationORM(
                food_item_id=str(fi.food_item_id),
                name=fi.name,
                description=fi.description,
            )
        )
    return translation


def _instruction_steps_to_orm(
    instructions: list | None,
) -> list[MealInstructionStepORM]:
    rows: list[MealInstructionStepORM] = []
    if not instructions:
        return rows
    for idx, item in enumerate(instructions):
        if isinstance(item, str):
            instruction = item.strip()
            duration = None
        elif isinstance(item, dict):
            instruction = str(
                item.get("instruction")
                or item.get("text")
                or item.get("description")
                or ""
            ).strip()
            duration = item.get("duration_minutes")
        else:
            continue
        if not instruction:
            continue
        rows.append(
            MealInstructionStepORM(
                id=str(uuid4()),
                instruction=instruction,
                duration_minutes=int(duration) if duration is not None else None,
                position=idx,
            )
        )
    return rows


def meal_domain_to_orm(domain: DomainMeal) -> MealORM:
    orm = MealORM(
        meal_id=str(domain.meal_id),
        user_id=str(domain.user_id),
        status=MealStatusMapper.to_db(domain.status),
        created_at=domain.created_at,
        updated_at=getattr(domain, "updated_at", None) or utc_now(),
        dish_name=getattr(domain, "dish_name", None),
        meal_type=getattr(domain, "meal_type", None),
        ready_at=getattr(domain, "ready_at", None),
        error_message=getattr(domain, "error_message", None),
        raw_ai_response=getattr(domain, "raw_gpt_json", None),
        food_label_metadata=getattr(domain, "food_label_metadata", None),
        last_edited_at=getattr(domain, "last_edited_at", None),
        edit_count=getattr(domain, "edit_count", 0),
        is_manually_edited=getattr(domain, "is_manually_edited", False),
        source=getattr(domain, "source", None),
        catalog_meal_id=getattr(domain, "catalog_meal_id", None),
        description=getattr(domain, "description", None),
        instructions=getattr(domain, "instructions", None),
        prep_time_min=getattr(domain, "prep_time_min", None),
        cook_time_min=getattr(domain, "cook_time_min", None),
        cuisine_type=getattr(domain, "cuisine_type", None),
        origin_country=getattr(domain, "origin_country", None),
        emoji=getattr(domain, "emoji", None),
        quantity=getattr(domain, "quantity", None),
    )
    if domain.image:
        orm.image_id = str(domain.image.image_id)
    if domain.nutrition:
        orm.nutrition = nutrition_domain_to_orm(domain.nutrition, meal_id=orm.meal_id)
    orm.instruction_steps = _instruction_steps_to_orm(domain.instructions)
    return orm


# ---------------------------------------------------------------------------
# Legacy class-based API (kept for backward compatibility during migration)
# ---------------------------------------------------------------------------


class MealMapper:
    """Mapper for Meal entity."""

    to_domain = staticmethod(meal_orm_to_domain)
    to_persistence = staticmethod(meal_domain_to_orm)


class MealImageMapper:
    """Mapper for MealImage entity."""

    to_domain = staticmethod(meal_image_orm_to_domain)
    to_persistence = staticmethod(meal_image_domain_to_orm)


class NutritionMapper:
    """Mapper for Nutrition entity."""

    to_domain = staticmethod(nutrition_orm_to_domain)
    to_persistence = staticmethod(
        lambda domain, meal_id: nutrition_domain_to_orm(domain, meal_id)
    )


class FoodItemMapper:
    """Mapper for FoodItem entity."""

    to_domain = staticmethod(food_item_orm_to_domain)
    to_persistence = staticmethod(
        lambda domain, nutrition_id: food_item_domain_to_orm(domain, nutrition_id)
    )


class MealTranslationMapper:
    """Mapper for MealTranslation entity."""

    to_domain = staticmethod(meal_translation_orm_to_domain)
    to_persistence = staticmethod(meal_translation_domain_to_orm)
