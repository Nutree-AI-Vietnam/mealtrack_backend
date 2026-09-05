"""Cover meal_suggestions routes with TestClient + rate limiter state."""

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from tests.fixtures.factories.nutrition_factory import NutritionFactory

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1 import meal_suggestions as ms_mod
from src.app.commands.meal_suggestion import (
    DiscoverMealsCommand,
    GenerateMealRecipesCommand,
    SaveMealSuggestionCommand,
)
from src.app.queries.meal import GetMealByIdQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.meal_suggestion.meal_suggestion import (
    Ingredient,
    MacroEstimate,
    MealSuggestion,
    MealType,
    RecipeStep,
)
from src.domain.model.meal_suggestion.suggestion_session import SuggestionSession


class _BusOk:
    async def send(self, msg):
        session = SuggestionSession(
            id="sess-1",
            user_id="user-1",
            meal_type="lunch",
            meal_portion_type="main",
            target_calories=500,
            ingredients=[],
            cooking_time_minutes=30,
        )
        sug = MealSuggestion(
            id="s1",
            session_id="sess-1",
            user_id="user-1",
            meal_name="Test Bowl",
            description="d",
            meal_type=MealType.LUNCH,
            macros=MacroEstimate(calories=400, protein=30, carbs=40, fat=12),
            ingredients=[],
            recipe_steps=[],
            prep_time_minutes=15,
            confidence_score=0.9,
        )
        return session, [sug]


@pytest.fixture
def ms_client():
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register_exception_handlers(app)
    app.include_router(ms_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    bus = _BusOk()
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    yield TestClient(app), bus
    app.dependency_overrides = {}


def test_generate_meal_suggestions_endpoint_removed(ms_client):
    client, _bus = ms_client
    payload = {
        "meal_type": "lunch",
        "meal_portion_type": "main",
        "ingredients": ["chicken"],
        "cooking_time_minutes": 30,
    }
    r = client.post("/v1/meal-suggestions/generate", json=payload)
    assert r.status_code == 404

    schema = client.get("/openapi.json").json()
    assert "/v1/meal-suggestions/generate" not in schema["paths"]


def test_save_meal_suggestion_ok(ms_client):
    client, bus = ms_client
    meal_id = "11111111-1111-4111-8111-111111111111"
    user_id = "22222222-2222-4222-8222-222222222222"

    class _BusSave:
        async def send(self, msg):
            if isinstance(msg, SaveMealSuggestionCommand):
                assert msg.ingredients[0].food_reference_id == 987
                assert msg.ingredients[0].fiber == 3
                assert msg.fiber == 4
                assert msg.calories == 157
                assert msg.meal_id == meal_id
                return meal_id
            if isinstance(msg, GetMealByIdQuery):
                return Meal(
                    meal_id=meal_id,
                    user_id=user_id,
                    status=MealStatus.READY,
                    created_at=datetime(2026, 4, 11, 12, 0, 0),
                    ready_at=datetime(2026, 4, 11, 12, 0, 1),
                    image=None,
                    dish_name="Saved",
                    meal_type="lunch",
                    nutrition=NutritionFactory.create_nutrition(),
                )
            raise AssertionError(f"unexpected bus message: {type(msg)!r}")

    # replace bus on app
    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BusSave()

    payload = {
        "suggestion_id": "s1",
        "meal_id": meal_id,
        "name": "Saved",
        "meal_type": "lunch",
        "protein": 10.0,
        "carbs": 20.0,
        "fat": 5.0,
        "fiber": 4.0,
        "ingredients": [
            {
                "name": "Chicken breast",
                "amount": 120,
                "unit": "g",
                "food_reference_id": 987,
                "protein": 10,
                "carbs": 0,
                "fat": 2,
                "fiber": 3,
            }
        ],
        "instructions": [],
        "meal_date": "2026-04-11",
    }
    r = client.post("/v1/meal-suggestions/save", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["meal_id"] == meal_id
    assert data["meal_detail"]["meal_id"] == meal_id
    assert data["meal_detail"]["food_items"]


def test_save_meal_suggestion_rejects_invalid_meal_id(ms_client):
    client, _bus = ms_client
    payload = {
        "suggestion_id": "s1",
        "meal_id": "not-a-uuid",
        "name": "Saved",
        "meal_type": "lunch",
        "protein": 10.0,
        "carbs": 20.0,
        "fat": 5.0,
        "fiber": 4.0,
        "ingredients": [],
        "instructions": [],
        "meal_date": "2026-04-11",
    }
    r = client.post("/v1/meal-suggestions/save", json=payload)
    assert r.status_code == 422


def test_discover_meals_sends_cqrs_command(ms_client, monkeypatch):
    client, _bus = ms_client
    captured = {}

    class _BusDiscover:
        async def send(self, msg):
            captured["msg"] = msg
            session = SuggestionSession(
                id="sess-discovery",
                user_id="user-1",
                meal_type="lunch",
                meal_portion_type="main",
                target_calories=500,
                ingredients=["tofu"],
                cooking_time_minutes=20,
                shown_meal_names=["Tofu Bowl"],
            )
            return session, [
                {
                    "id": "disc_a",
                    "name": "Tofu Bowl",
                    "english_name": "Tofu Bowl",
                    "calories": 450,
                    "protein": 35,
                    "carbs": 45,
                    "fat": 14,
                }
            ]

    class _Images:
        def __init__(self):
            self.names = []

        async def search_food_image(self, name):
            self.names.append(name)
            return None

    images_service = _Images()

    monkeypatch.setattr(
        "src.api.dependencies.food_image.get_food_image_service",
        lambda: images_service,
    )
    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BusDiscover()

    payload = {
        "meal_type": "lunch",
        "meal_portion_type": "main",
        "ingredients": ["tofu"],
        "cooking_time_minutes": 20,
        "batch_size": 10,
        "cuisine_region": "japanese",
        "calorie_target": 450,
    }

    r = client.post("/v1/meal-suggestions/discover", json=payload)

    assert r.status_code == 200
    assert isinstance(captured["msg"], DiscoverMealsCommand)
    assert captured["msg"].meal_type == "lunch"
    assert captured["msg"].count == 10
    assert r.json()["meals"][0]["id"] == "disc_a"
    assert images_service.names == ["Tofu Bowl"]


def test_generate_recipes_accepts_selected_discovery_ids(ms_client):
    client, _bus = ms_client
    captured = {}

    class _BusRecipe:
        async def send(self, msg):
            captured["msg"] = msg
            return [
                MealSuggestion(
                    id="s1",
                    session_id="recipe-session",
                    user_id="user-1",
                    meal_name="Lemongrass Carp Grilled",
                    description="",
                    meal_type=MealType.LUNCH,
                    macros=MacroEstimate(calories=350, protein=35, carbs=20, fat=12),
                    ingredients=[Ingredient(name="carp", amount=120, unit="g")],
                    recipe_steps=[
                        RecipeStep(
                            step=1,
                            instruction="Steam the carp.",
                            duration_minutes=15,
                        )
                    ],
                    prep_time_minutes=20,
                    confidence_score=0.9,
                )
            ]

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BusRecipe()

    payload = {
        "session_id": "sess-discovery",
        "selected_meal_ids": ["disc_b", "disc_a"],
        "meal_type": "lunch",
    }

    r = client.post("/v1/meal-suggestions/recipes", json=payload)

    assert r.status_code == 200
    assert isinstance(captured["msg"], GenerateMealRecipesCommand)
    assert captured["msg"].session_id == "sess-discovery"
    assert captured["msg"].selected_meal_ids == ["disc_b", "disc_a"]
    assert r.json()["recipes"][0]["meal_name"] == "Lemongrass Carp Grilled"


def test_generate_recipes_accepts_full_selected_meals_when_session_missing(ms_client):
    client, _bus = ms_client
    captured = {}

    class _BusRecipe:
        async def send(self, msg):
            captured["msg"] = msg
            return [
                MealSuggestion(
                    id="s1",
                    session_id="recipe-session",
                    user_id="user-1",
                    meal_name="Ginger Carp Steamed",
                    description="",
                    meal_type=MealType.LUNCH,
                    macros=MacroEstimate(calories=300, protein=32, carbs=18, fat=10),
                    ingredients=[Ingredient(name="carp", amount=120, unit="g")],
                    recipe_steps=[
                        RecipeStep(
                            step=1,
                            instruction="Steam the carp.",
                            duration_minutes=15,
                        )
                    ],
                    prep_time_minutes=20,
                    confidence_score=0.9,
                )
            ]

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BusRecipe()

    payload = {
        "session_id": "expired-session",
        "selected_meal_ids": ["disc_a"],
        "selected_meals": [
            {
                "id": "disc_a",
                "meal_name": "Ginger Carp Steamed",
                "english_name": "Ginger Carp Steamed",
                "macros": {
                    "calories": 300,
                    "protein": 32,
                    "carbs": 18,
                    "fat": 10,
                },
            }
        ],
        "meal_names": ["Ginger Carp Steamed"],
        "meal_type": "lunch",
    }

    r = client.post("/v1/meal-suggestions/recipes", json=payload)

    assert r.status_code == 200
    assert isinstance(captured["msg"], GenerateMealRecipesCommand)
    assert captured["msg"].session_id == "expired-session"
    assert captured["msg"].selected_meal_ids == ["disc_a"]
    assert captured["msg"].selected_meals == payload["selected_meals"]


def test_generate_recipes_generation_failure_returns_503(ms_client):
    client, _bus = ms_client

    class _BusRecipeFailure:
        async def send(self, msg):
            from src.api.exceptions import ExternalServiceException

            raise ExternalServiceException(
                "Could not generate recipes. Please retry.",
                error_code="RECIPE_GENERATION_FAILED",
                details={"requested": 3, "generated": 0},
            )

    client.app.dependency_overrides[get_configured_event_bus] = lambda: (
        _BusRecipeFailure()
    )

    payload = {
        "session_id": "sess-discovery",
        "selected_meal_ids": ["disc_a", "disc_b", "disc_c"],
        "selected_meals": [
            {
                "id": "disc_a",
                "meal_name": "Ginger Chicken Rice",
                "english_name": "Ginger Chicken Rice",
                "macros": {
                    "calories": 450,
                    "protein": 35,
                    "carbs": 45,
                    "fat": 12,
                },
            },
            {
                "id": "disc_b",
                "meal_name": "Lemon Salmon Bowl",
                "english_name": "Lemon Salmon Bowl",
                "macros": {
                    "calories": 520,
                    "protein": 38,
                    "carbs": 50,
                    "fat": 18,
                },
            },
            {
                "id": "disc_c",
                "meal_name": "Tofu Vegetable Noodles",
                "english_name": "Tofu Vegetable Noodles",
                "macros": {
                    "calories": 430,
                    "protein": 28,
                    "carbs": 55,
                    "fat": 11,
                },
            },
        ],
        "meal_names": [
            "Ginger Chicken Rice",
            "Lemon Salmon Bowl",
            "Tofu Vegetable Noodles",
        ],
        "meal_type": "lunch",
    }

    response = client.post("/v1/meal-suggestions/recipes", json=payload)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "RECIPE_GENERATION_FAILED"
