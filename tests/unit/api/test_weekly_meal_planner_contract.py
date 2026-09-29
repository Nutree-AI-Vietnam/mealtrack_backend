import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_text_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.routes.v1.meal_plans import router
from src.app.handlers.query_handlers.meal_planner.weekly_meal_planner_query_handlers import (
    ListRecipesQueryHandler,
)
from src.app.queries.meal_planner import ListRecipesQuery
from src.domain.model.meal_recommendation.catalog_recipe import (
    CatalogMeal,
    CatalogMealIngredient,
)
from src.domain.model.nutrition.micros import Micros
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.services.weekly_meal_planner.grocery_projection import (
    deterministic_ingredient_id,
)


def _plan():
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=2,
        preferences=WeeklyMealPlanPreferences(people=2),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id=None,
            )
            for day in range(7)
            for slot in range(2)
        ),
        created_at=datetime(2026, 9, 20),
        updated_at=datetime(2026, 9, 20),
    )


class _Bus:
    def __init__(self, recipe=None, proposal=None):
        self.recipe = recipe
        self.proposal = proposal
        self.recipe_query = None

    async def send(self, query):
        if query.__class__.__name__ == "GetUserTimezoneQuery":
            return "UTC"
        if query.__class__.__name__ == "GetCurrentWeeklyPlanQuery":
            return _plan()
        if query.__class__.__name__ == "GetRecipeDetailQuery":
            return self.recipe
        if query.__class__.__name__ == "ListRecipesQuery":
            self.recipe_query = query
            return SimpleNamespace(items=(), total=0)
        if query.__class__.__name__ == "GetWeeklyGroceriesQuery":
            return (_plan(), ())
        if query.__class__.__name__ == "AiAdjustMealPlanCommand":
            return self.proposal
        raise AssertionError(f"unexpected query: {query!r}")


class _FoodReferenceRepository:
    def __init__(self, projections=None):
        self.projections = projections or {}
        self.display_projection_request = None

    async def get_display_projections(self, ids, language=None):
        self.display_projection_request = (ids, language)
        return self.projections


def _app(recipe=None, bus=None, food_reference_repository=None):
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    event_bus = bus or _Bus(recipe)
    app.dependency_overrides[get_configured_event_bus] = lambda: event_bus
    app.dependency_overrides[get_text_translation_service] = lambda: None
    app.dependency_overrides[get_async_food_reference_repository] = lambda: (
        food_reference_repository or _FoodReferenceRepository()
    )
    return app


def _recipe(micros=None):
    return CatalogMeal(
        id="catalog-1",
        catalog_key="sample-recipe",
        content_hash="a" * 64,
        name="Sample recipe",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=30,
        carbs_g=42,
        fat_g=14,
        fiber_g=8,
        nutrition_micros=micros,
    )


def test_current_plan_returns_fourteen_slots():
    response = TestClient(_app()).get(
        "/v1/meal-plans/current?week_start_date=2026-09-21"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert len(body["plan"]) == 7
    assert sum(len(day) for day in body["plan"]) == 14
    assert body["plan"][0][0]["slot_name"] == "lunch"
    assert body["plan"][0][1]["slot_name"] == "dinner"


def test_current_plan_without_week_start_resolves_to_current_monday():
    response = TestClient(_app()).get("/v1/meal-plans/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-1"
    assert "to_buy_count" in body


def test_current_plan_rejects_non_monday_week():
    response = TestClient(_app()).get(
        "/v1/meal-plans/current?week_start_date=2026-09-22"
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "WEEK_START_NOT_MONDAY"


def test_recipe_detail_exposes_complete_micros_and_nrf_score():
    response = TestClient(_app(_recipe(Micros(iron=4, potassium=500)))).get(
        "/v1/recipes/catalog-1"
    )

    assert response.status_code == 200
    nutrition = response.json()["nutrition_per_serving"]
    assert nutrition["micros"] == {"iron": 4.0, "potassium": 500.0}
    assert isinstance(nutrition["score"], int)
    assert 0 <= nutrition["score"] <= 100


def test_recipe_detail_uses_empty_micros_and_null_score_when_unavailable():
    response = TestClient(_app(_recipe())).get("/v1/recipes/catalog-1")

    assert response.status_code == 200
    nutrition = response.json()["nutrition_per_serving"]
    assert nutrition["micros"] == {}
    assert nutrition["score"] is None


def test_recipe_detail_unmapped_ingredient_id_is_not_string_none():
    ingredient = CatalogMealIngredient(
        food_reference_id=None,
        display_name="Phi lê cá điêu hồng",
        quantity=Decimal("300"),
        unit="g",
        category="protein",
    )
    meal = CatalogMeal(
        id="catalog-1",
        catalog_key="sample-recipe",
        content_hash="a" * 64,
        name="Sample recipe",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("42"),
        fat_g=Decimal("14"),
        fiber_g=Decimal("8"),
        ingredients=(ingredient,),
    )
    response = TestClient(_app(meal)).get("/v1/recipes/catalog-1")
    assert response.status_code == 200
    ing_response = response.json()["ingredients"][0]
    assert ing_response["id"] != "None"
    assert ing_response["id"] == str(
        deterministic_ingredient_id("Phi lê cá điêu hồng")
    )


def test_recipe_list_forwards_requested_meal_type():
    for meal_type in ("lunch", "dinner"):
        bus = _Bus()
        response = TestClient(_app(bus=bus)).get(f"/v1/recipes?meal_type={meal_type}")

        assert response.status_code == 200
        assert bus.recipe_query.meal_type == meal_type


def test_recipe_list_rejects_unsupported_meal_type():
    response = TestClient(_app()).get("/v1/recipes?meal_type=snack")

    assert response.status_code == 422


def test_recipe_query_passes_meal_type_to_catalog_repository():
    class _CatalogRepository:
        meal_type = None

        async def list_active_meals(self, *, cuisine=None, meal_type=None):
            self.meal_type = meal_type
            return []

    class _UnitOfWork:
        def __init__(self, catalog_repository):
            self.catalog_recipes = catalog_repository

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    catalog_repository = _CatalogRepository()
    handler = ListRecipesQueryHandler(lambda: _UnitOfWork(catalog_repository))

    asyncio.run(handler.handle(ListRecipesQuery(meal_type="dinner")))

    assert catalog_repository.meal_type == "dinner"


def test_vietnamese_ai_proposal_uses_catalog_grocery_name_vi():
    item = SimpleNamespace(
        ingredient_id=42,
        name="Tomato",
        category="fresh_produce",
        total_needed=850.0,
        unit="g",
    )
    proposal = SimpleNamespace(
        base_revision=3,
        explanation="One dinner changes.",
        diff_summary="1 meal changed",
        proposed_plan=_plan(),
        slot_changes=(),
        proposed_groceries=(item,),
    )
    repository = _FoodReferenceRepository(
        {42: {"name": "Tomato", "name_vi": "Cà chua", "serving_labels": {}}}
    )
    response = TestClient(
        _app(
            bus=_Bus(proposal=proposal),
            food_reference_repository=repository,
        )
    ).post(
        "/v1/meal-plans/plan-1/ai-prompt",
        headers={"Accept-Language": "vi"},
        json={"prompt": "Thêm cà chua"},
    )

    assert response.status_code == 200
    grocery = response.json()["proposed_groceries"][0]
    assert grocery == {
        "ingredient_id": 42,
        "name": "Cà chua",
        "category": "fresh_produce",
        "total_needed": 850.0,
        "unit": "g",
    }
    assert repository.display_projection_request == ([42], "en")


def test_patch_rejects_duplicate_slot_coordinates_before_dispatch():
    response = TestClient(_app()).patch(
        "/v1/meal-plans/plan-1",
        headers={"Idempotency-Key": "patch-1"},
        json={
            "slots": [
                {"day_index": 0, "slot_index": 0, "recipe_id": None},
                {"day_index": 0, "slot_index": 0, "recipe_id": None},
            ]
        },
    )

    assert response.status_code == 422


def test_patch_rejects_partial_preference_payloads_before_dispatch():
    response = TestClient(_app()).patch(
        "/v1/meal-plans/plan-1",
        headers={"Idempotency-Key": "patch-partial"},
        json={"people": 2, "preferences": {"diet": "vegetarian"}},
    )

    assert response.status_code == 422


def test_patch_requires_people_when_preferences_are_updated():
    response = TestClient(_app()).patch(
        "/v1/meal-plans/plan-1",
        headers={"Idempotency-Key": "patch-no-people"},
        json={
            "preferences": {
                "diet": "vegetarian",
                "cooking_time": "any",
                "cuisine": None,
                "dislikes": "",
                "allergies": "",
            }
        },
    )

    assert response.status_code == 422
