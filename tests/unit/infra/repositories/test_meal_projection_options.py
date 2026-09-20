"""Unit tests for Meal repository projection options and mapper deferral handling."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm.strategy_options import Load

from src.domain.model.meal_projection import MealProjection
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.mappers.meal_mapper import meal_orm_to_domain
from src.infra.repositories.meal_repository_async import _PROJECTION_OPTS


def test_macros_only_projection_defers_heavy_columns():
    options = _PROJECTION_OPTS[MealProjection.MACROS_ONLY]
    load_elements = [opt for opt in options if isinstance(opt, Load)]
    assert len(load_elements) >= 3

    deferred_paths = [
        str(ctx.path)
        for opt in load_elements
        for ctx in getattr(opt, "context", ())
        if hasattr(ctx, "path")
    ]
    assert any("raw_ai_response" in p for p in deferred_paths)
    assert any("food_label_metadata" in p for p in deferred_paths)
    assert any("instructions" in p for p in deferred_paths)


def test_projection_variants_are_distinct():
    assert MealProjection.MACROS_WITH_MICROS in _PROJECTION_OPTS
    assert MealProjection.LIST_CARD in _PROJECTION_OPTS
    assert (
        _PROJECTION_OPTS[MealProjection.MACROS_ONLY]
        != _PROJECTION_OPTS[MealProjection.MACROS_WITH_MICROS]
    )
    assert (
        _PROJECTION_OPTS[MealProjection.LIST_CARD]
        != _PROJECTION_OPTS[MealProjection.FULL_WITH_TRANSLATIONS]
    )


def test_meal_orm_to_domain_handles_deferred_attributes():
    meal_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    orm = MealORM(
        meal_id=meal_id,
        user_id=user_id,
        status=MealStatusEnum.PROCESSING,
    )
    orm.created_at = datetime.now(UTC)
    orm.image = None
    orm.nutrition = None
    orm.translations = []
    orm.instruction_steps = []

    # Ensure deferred attributes are absent from instance __dict__
    orm.__dict__.pop("raw_ai_response", None)
    orm.__dict__.pop("food_label_metadata", None)
    orm.__dict__.pop("description", None)
    orm.__dict__.pop("instructions", None)

    domain = meal_orm_to_domain(orm)
    assert domain.meal_id == meal_id
    assert domain.raw_gpt_json is None
    assert domain.food_label_metadata is None
    assert domain.description is None
    assert domain.instructions is None


def test_meal_orm_to_domain_maps_loaded_attributes():
    meal_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    orm = MealORM(
        meal_id=meal_id,
        user_id=user_id,
        status=MealStatusEnum.PROCESSING,
        raw_ai_response='{"analysis": "test"}',
        description="Delicious meal",
    )
    orm.created_at = datetime.now(UTC)
    orm.image = None
    orm.nutrition = None
    orm.translations = []
    orm.instruction_steps = []

    domain = meal_orm_to_domain(orm)
    assert domain.meal_id == meal_id
    assert domain.raw_gpt_json == '{"analysis": "test"}'
    assert domain.description == "Delicious meal"
