"""Contract tests for recipe publish, grocery derivation, and logged snapshots."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.ports.catalog_recipe_repository_port import (
    CatalogMealSeedIngredientWrite,
    CatalogMealSeedWrite,
)
from src.infra.database.base import Base
from src.infra.database.models.food_reference_alias import FoodReferenceAliasORM
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.meal_recommendation import (
    AllergenReferenceORM,
    MealCatalogORM,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    recipe_excluded_by_allergen,
)
from src.domain.services.weekly_meal_planner.food_alias import resolve_food_alias
from src.domain.services.weekly_meal_planner.meal_log_snapshot import (
    build_logged_meal_snapshot,
    select_logged_nutrition,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    is_planner_eligible,
    payload_digest,
    projection_nutrition_quantity,
    publish_recipe,
)
from src.domain.services.weekly_meal_planner.slot_rules import (
    ensure_base_revision,
    ensure_slot_coordinate,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    WeeklyPlanGenerationService,
)

_USER_ID = "11111111-1111-1111-1111-111111111111"


def _ingredient(food_id: int, name: str, quantity: str, *, position: int) -> CatalogMealIngredient:
    return CatalogMealIngredient(
        food_reference_id=food_id,
        display_name=name,
        quantity=Decimal(quantity),
        unit="g",
        category="produce",
        position=position,
    )


def _meal(**overrides) -> CatalogMeal:
    payload = {
        "recipe_name": "Tomato plate",
        "ingredients": [
            {"name": "Tomato", "quantity": 1200, "unit": "g", "food_reference_id": 7},
            {"name": "Muối", "quantity": None, "unit": None, "quantity_text": "theo khẩu vị"},
        ],
    }
    values = {
        "id": "recipe-1",
        "catalog_key": "recipe-1",
        "content_hash": "a" * 64,
        "name": "Tomato plate",
        "cuisine": "vietnamese",
        "description": None,
        "image_url": None,
        "protein_g": Decimal("40"),
        "carbs_g": Decimal("20"),
        "fat_g": Decimal("10"),
        "fiber_g": Decimal("4"),
        "base_servings": 1,
        "serving_confidence": "verified",
        "meal_types": ("lunch", "dinner"),
        "publication_status": "published",
        "nutrition_status": "ready",
        "recipe_payload": payload,
        "ingredients": (_ingredient(7, "Tomato", "1200", position=1),),
    }
    values.update(overrides)
    return CatalogMeal(**values)


def _plan(recipe_id: str | None = "recipe-1", override: dict | None = None) -> WeeklyMealPlan:
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=WeeklyMealPlanPreferences(people=1),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id=recipe_id if day == 0 and slot == 0 else None,
                recipe_override=override if day == 0 and slot == 0 else None,
            )
            for day in range(7)
            for slot in range(2)
        ),
    )


class _Catalog:
    def __init__(self, meal: CatalogMeal | None):
        self.meal = meal

    async def get_meal(self, recipe_id):
        if self.meal is None or self.meal.id != recipe_id:
            return None
        return self.meal


class _Plans:
    def __init__(self, pantry: list[dict], interactions: list[dict] | None = None):
        self.pantry = pantry
        self.interactions = interactions or []

    async def list_pantry(self, **kwargs):
        return self.pantry

    async def list_grocery_interactions(self, **kwargs):
        return self.interactions


def _uow(meal, pantry, interactions=None):
    class _Uow:
        catalog_recipes = _Catalog(meal)
        weekly_meal_plans = _Plans(pantry, interactions)

    return _Uow()


async def _item(meal, pantry, interactions=None, override=None):
    categories = await WeeklyGroceryService().calculate(
        _uow(meal, pantry, interactions), _plan(override=override)
    )
    if not categories:
        return None
    return categories[0].items[0]


@pytest.mark.asyncio
async def test_repeated_ingredients_keep_order_and_distinct_quantities():
    meal = _meal(
        ingredients=(
            _ingredient(7, "Olive oil", "10", position=1),
            _ingredient(7, "Olive oil", "20", position=2),
        ),
        recipe_payload={"recipe_name": "Oil", "ingredients": []},
    )

    item = await _item(meal, [])

    assert item.contributions == ((1, 10.0), (2, 20.0))
    assert item.total_needed == 30
    assert item.status == "needed"
    assert item.remaining == 30


def test_to_taste_lines_add_nothing_to_grocery_or_nutrition():
    salt = {"name": "Muối", "quantity": None, "unit": None, "quantity_text": "theo khẩu vị"}
    tomato = {"name": "Tomato", "food_reference_id": 7, "quantity": 100, "unit": "g"}

    assert projection_nutrition_quantity(salt) == 0
    assert projection_nutrition_quantity(tomato) == Decimal("100")
    published = publish_recipe(
        recipe_name="Tomato plate",
        description=None,
        ingredients=[tomato, salt],
        instructions=[{"step": 1, "title": "Plate", "instruction": "Serve"}],
        nutrition_ready=True,
    )
    assert [line.food_reference_id for line in published.ingredients] == [7]
    assert published.recipe_payload["ingredients"][1]["quantity"] is None
    assert published.payload_digest == payload_digest(published.recipe_payload)


@pytest.mark.asyncio
async def test_unpublished_or_unready_recipes_are_absent_from_grocery_and_planning():
    draft = _meal(publication_status="draft")
    unready = _meal(id="recipe-2", nutrition_status="not_ready")

    assert await _item(draft, []) is None
    assert await _item(unready, []) is None
    assert is_planner_eligible(publication_status="draft", nutrition_status="ready") is False
    assert is_planner_eligible(publication_status="published", nutrition_status="not_ready") is False
    generated = WeeklyPlanGenerationService().generate(
        [draft, unready],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=1800,
        preferences=WeeklyMealPlanPreferences(),
    )
    assert all(slot.recipe_id is None for slot in generated)


@pytest.mark.asyncio
async def test_pantry_quantity_sets_needed_need_more_and_owned_without_stock_kind():
    meal = _meal()
    partial = await _item(meal, [{"food_reference_id": 7, "available_amount": Decimal("400"), "available_unit": "g"}])
    covered = await _item(meal, [{"food_reference_id": 7, "available_amount": Decimal("1200"), "available_unit": "g"}])
    empty = await _item(meal, [])

    assert partial.status == "need_more"
    assert partial.remaining == 800
    assert partial.total_needed == 1200
    assert covered.status == "owned"
    assert covered.remaining == 0
    assert empty.status == "needed"
    assert empty.remaining == 1200
    assert not hasattr(partial, "stock_kind")


@pytest.mark.asyncio
async def test_interaction_flags_do_not_change_derived_quantity():
    meal = _meal()
    item = await _item(
        meal,
        [{"food_reference_id": 7, "available_amount": Decimal("400"), "available_unit": "g"}],
        [
            {
                "food_reference_id": 7,
                "checked": True,
                "do_not_buy": True,
                "manually_owned": True,
            }
        ],
    )

    assert item.remaining == 800
    assert item.total_needed == 1200
    assert item.status == "need_more"
    assert item.checked is True
    assert item.do_not_buy is True
    assert item.manually_owned is True


@pytest.mark.asyncio
async def test_slot_override_changes_grocery_without_rewriting_catalog_payload():
    meal = _meal()
    original = {
        "recipe_name": meal.recipe_payload["recipe_name"],
        "ingredients": [dict(line) for line in meal.recipe_payload["ingredients"]],
    }
    item = await _item(
        meal,
        [],
        override={
            "ingredient_changes": [
                {"position": 1, "new_quantity": 500, "unit": "g"}
            ]
        },
    )

    assert item.total_needed == 500
    assert meal.recipe_payload == original


def test_publish_resolves_alias_and_keeps_payload_with_projections():
    published = publish_recipe(
        recipe_name="Bacon",
        description=None,
        ingredients=[
            {"name": "Thịt xông khói", "quantity": 20, "unit": "g"},
            {"name": "Muối", "quantity": None, "quantity_text": "to taste"},
        ],
        instructions=[],
        nutrition_ready=True,
        aliases=[("thịt xông khói", 9), ("smoked bacon", 9)],
    )

    assert resolve_food_alias("Thịt xông khói", [("thịt xông khói", 9)]) == 9
    assert published.ingredients[0].food_reference_id == 9
    assert published.ingredients[0].position == 1
    assert len(published.ingredients) == 1
    assert published.publication_status == "published"
    assert published.nutrition_status == "ready"
    assert published.planner_eligible is True


def test_normalized_allergen_excludes_recipe_and_free_text_does_not():
    peanut = _meal(id="peanut", name="Salad", allergen_codes=("peanut",), allergens="none")
    labeled_only = _meal(id="labeled", name="Noodles", allergens="peanut", allergen_codes=())
    preferences = WeeklyMealPlanPreferences(allergies=("Peanut",))

    assert recipe_excluded_by_allergen(peanut.allergen_codes, preferences.allergies)
    assert not recipe_excluded_by_allergen(labeled_only.allergen_codes, preferences.allergies)
    generated = WeeklyPlanGenerationService().generate(
        [peanut, labeled_only],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=1800,
        preferences=preferences,
    )
    assert {slot.recipe_id for slot in generated} == {"labeled"}


def test_logged_snapshot_keeps_old_provenance_and_prefers_verified_nutrition():
    payload = {
        "recipe_name": "Cá diêu hồng",
        "ingredients": [{"name": "Cá", "quantity": 250, "unit": "g"}],
    }
    verified = {"calories": 487, "protein_g": 42.3, "carbs_g": 31.2, "fat_g": 19.1}
    estimate = {"calories": 900, "protein_g": 1, "carbs_g": 1, "fat_g": 1}
    chosen = select_logged_nutrition(verified=verified, estimate=estimate)
    marked = select_logged_nutrition(verified=None, estimate=estimate)
    snapshot = build_logged_meal_snapshot(
        catalog_meal_id="recipe-1",
        content_hash="b" * 64,
        recipe_payload=payload,
        nutrition=chosen,
    )
    payload["recipe_name"] = "Changed later"
    payload["ingredients"][0]["quantity"] = 1

    assert chosen["estimated"] is False
    assert chosen["source"] == "verified_database"
    assert chosen["calories"] == 487
    assert marked["estimated"] is True
    assert marked["source"] == "ai_estimate"
    assert snapshot.recipe_snapshot["recipe_name"] == "Cá diêu hồng"
    assert snapshot.recipe_snapshot["ingredients"][0]["quantity"] == 250
    assert snapshot.nutrition_snapshot["calories"] == 487
    assert snapshot.catalog_meal_content_hash == "b" * 64


@pytest.mark.asyncio
async def test_materializer_persists_verified_snapshot_not_the_estimate():
    payload = {"recipe_name": "Tomato plate", "ingredients": [{"name": "Tomato", "quantity": 100, "unit": "g"}]}
    meal = _meal(
        recipe_payload=payload,
        ai_nutrition_estimate={"calories": 999, "protein_g": 1, "carbs_g": 1, "fat_g": 1},
    )

    class _Meals:
        async def save(self, meal):
            return meal

    class _Foods:
        pass

    class _Uow:
        meals = _Meals()
        food_references = _Foods()

    logged = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=meal,
        meal_date=date(2026, 9, 21),
        meal_type="lunch",
        timezone="UTC",
    )
    payload["recipe_name"] = "Changed later"

    assert logged.nutrition_snapshot["estimated"] is False
    assert logged.nutrition_snapshot["source"] == "verified_database"
    assert logged.nutrition_snapshot["calories"] == meal.calories
    assert logged.nutrition_snapshot["calories"] != 999
    assert logged.recipe_snapshot["recipe_name"] == "Tomato plate"
    assert logged.catalog_meal_content_hash == "a" * 64
    assert float(logged.nutrition.macros.protein) == 40


class _SyncAsyncSession:
    """Async session surface over a real sqlite session. The repository stays real."""

    def __init__(self, session: Session):
        self._session = session

    async def execute(self, statement, *args, **kwargs):
        return self._session.execute(statement, *args, **kwargs)

    def add(self, instance):
        self._session.add(instance)

    async def flush(self):
        self._session.flush()


def _seed(catalog_key: str, *, name: str, allergens: str, ingredient_name: str) -> CatalogMealSeedWrite:
    return CatalogMealSeedWrite(
        catalog_key=catalog_key,
        content_hash=("d" if catalog_key == "bacon-roll" else "e") * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        meal_types=("lunch", "dinner"),
        allergens=allergens,
        ingredients=(
            CatalogMealSeedIngredientWrite(
                display_name=ingredient_name,
                quantity=Decimal("20"),
                unit="g",
                category="protein",
            ),
        ),
        base_servings=1,
        serving_source="explicit",
        serving_confidence="verified",
    )


@pytest.mark.asyncio
async def test_seed_publish_uses_stored_aliases_and_allergen_links():
    import src.infra.database.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            food = FoodReferenceModel(
                name="Bacon",
                protein_100g=10,
                carbs_100g=0,
                fat_100g=40,
            )
            session.add(food)
            session.flush()
            session.add(
                FoodReferenceAliasORM(
                    alias="Thịt xông khói",
                    normalized_alias="thịt xông khói",
                    food_reference_id=food.id,
                    language="vi",
                )
            )
            session.add(AllergenReferenceORM(code="peanut", name="Peanut"))
            session.flush()
            repository = AsyncCatalogMealRepository(_SyncAsyncSession(session))
            await repository.add_seed_meal(
                _seed(
                    "bacon-roll",
                    name="Bacon roll",
                    allergens="peanut, contains nuts",
                    ingredient_name="Thịt xông khói",
                )
            )
            await repository.add_seed_meal(
                _seed(
                    "plain-roll",
                    name="Plain roll",
                    allergens="contains nuts",
                    ingredient_name="Unknown herb",
                )
            )
            session.expire_all()
            bacon_id = session.execute(
                select(MealCatalogORM.id).where(MealCatalogORM.catalog_key == "bacon-roll")
            ).scalar_one()
            plain_id = session.execute(
                select(MealCatalogORM.id).where(MealCatalogORM.catalog_key == "plain-roll")
            ).scalar_one()
            bacon = await repository.get_meal(bacon_id)
            plain = await repository.get_meal(plain_id)

        assert bacon is not None and plain is not None
        assert bacon.ingredients[0].food_reference_id == food.id
        assert bacon.allergen_codes == ("peanut",)
        assert bacon.allergens == "peanut, contains nuts"
        assert plain.allergen_codes == ()
        assert plain.ingredients == ()
        generated = WeeklyPlanGenerationService().generate(
            [bacon, plain],
            user_id="user-1",
            week_start_date="2026-09-21",
            daily_calories=1800,
            preferences=WeeklyMealPlanPreferences(allergies=("peanut",)),
        )
        assert {slot.recipe_id for slot in generated} == {plain.id}
    finally:
        engine.dispose()


def test_stale_revision_and_slot_coordinates_are_rejected():
    with pytest.raises(ValueError, match="stale base_revision"):
        ensure_base_revision(1, 2)
    ensure_base_revision(3, 3)
    with pytest.raises(ValueError, match="slot coordinate"):
        ensure_slot_coordinate(7, 0)
    with pytest.raises(ValueError, match="slot coordinate"):
        ensure_slot_coordinate(0, 2)
    ensure_slot_coordinate(6, 1)
