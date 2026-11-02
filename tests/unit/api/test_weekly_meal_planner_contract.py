import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_planner_translation_service,
    get_text_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.routes.v1.meal_plans import WEEKLY_PLAN_FEATURES, router
from src.app.handlers.query_handlers.meal_planner.weekly_meal_planner_query_handlers import (
    ListRecipesQueryHandler,
)
from src.app.queries.meal_planner import ListRecipesQuery
from src.app.services.weekly_grocery_service import GroceryCategory, GroceryItem
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.meal_recommendation.catalog_recipe import (
    CatalogMeal,
    CatalogMealIngredient,
)
from src.domain.model.nutrition.micros import Micros
from src.domain.model.weekly_meal_planner import (
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
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
            for slot in range(WEEKLY_SLOTS_PER_DAY)
        ),
        created_at=datetime(2026, 9, 20),
        updated_at=datetime(2026, 9, 20),
    )


_DEFAULT_PLAN = object()


class _Bus:
    def __init__(self, recipe=None, proposal=None, plan=_DEFAULT_PLAN):
        self.recipe = recipe
        self.proposal = proposal
        self.plan = _plan() if plan is _DEFAULT_PLAN else plan
        self.recipe_query = None
        self.recipe_detail_query = None
        self.budget_queries = []
        self.generate_commands = []
        self.current_queries = []
        self.grocery_queries = []

    async def send(self, query):
        if query.__class__.__name__ == "GetUserTimezoneQuery":
            return "UTC"
        if query.__class__.__name__ == "GetCurrentWeeklyPlanQuery":
            self.current_queries.append(query)
            return self.plan
        if query.__class__.__name__ == "GetWeeklyBudgetQuery":
            self.budget_queries.append(query)
            return {"adjusted_daily_calories": 2100}
        if query.__class__.__name__ == "GenerateWeeklyMealPlanCommand":
            self.generate_commands.append(query)
            return _plan()
        if query.__class__.__name__ == "GetRecipeSummariesQuery":
            return ()
        if query.__class__.__name__ == "EnrichWeeklyPlanMicronutrientsCommand":
            return None
        if query.__class__.__name__ == "GetRecipeDetailQuery":
            self.recipe_detail_query = query
            return self.recipe
        if query.__class__.__name__ == "ListRecipesQuery":
            self.recipe_query = query
            return SimpleNamespace(items=(), total=0)
        if query.__class__.__name__ == "GetWeeklyGroceriesQuery":
            self.grocery_queries.append(query)
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


def _app(
    recipe=None,
    bus=None,
    food_reference_repository=None,
    authenticated=True,
    plan=_DEFAULT_PLAN,
    translation_service=None,
):
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    app.include_router(router)
    if authenticated:
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    event_bus = bus or _Bus(recipe, plan=plan)
    app.dependency_overrides[get_configured_event_bus] = lambda: event_bus
    app.dependency_overrides[get_text_translation_service] = lambda: None
    app.dependency_overrides[get_planner_translation_service] = lambda: (
        translation_service
    )
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


def test_current_plan_returns_three_meals_each_day():
    response = TestClient(_app()).get(
        "/v1/meal-plans/current?week_start_date=2026-09-21"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert len(body["plan"]) == 7
    assert sum(len(day) for day in body["plan"]) == WEEKLY_PLAN_SLOT_COUNT
    assert body["plan"][0][0]["slot_name"] == "breakfast"
    assert body["plan"][0][1]["slot_name"] == "lunch"
    assert body["plan"][0][2]["slot_name"] == "dinner"


def test_current_plan_opens_next_week_but_not_the_one_after():
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    client = TestClient(_app())

    def status_for(week_start):
        return client.get(
            f"/v1/meal-plans/current?week_start_date={week_start.isoformat()}",
            headers={"X-Timezone": "UTC"},
        ).status_code

    assert status_for(monday + timedelta(days=7)) == 200
    assert status_for(monday + timedelta(days=14)) == 400


def test_current_plan_says_how_the_week_was_selected():
    plan = replace(_plan(), algorithm_version="v3-compact")

    response = TestClient(_app(bus=_Bus(plan=plan))).get(
        "/v1/meal-plans/current?week_start_date=2026-09-21"
    )

    assert response.json()["algorithm_version"] == "v3-compact"


def test_current_plan_without_week_start_resolves_to_current_monday():
    response = TestClient(_app()).get("/v1/meal-plans/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-1"
    assert "to_buy_count" in body


def test_current_plan_auto_generates_when_missing():
    bus = _Bus(plan=None)
    response = TestClient(_app(bus=bus)).get("/v1/meal-plans/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-1"
    assert len(bus.generate_commands) == 1
    assert bus.generate_commands[0].daily_calories == 2100
    assert bus.generate_commands[0].preferences.people == 1


def test_current_plan_returns_404_when_missing_and_auto_generate_false():
    bus = _Bus(plan=None)
    response = TestClient(_app(bus=bus)).get(
        "/v1/meal-plans/current?auto_generate=false"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Weekly meal plan not found"
    assert len(bus.generate_commands) == 0


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("include_count", [False, True])
def test_current_plan_legacy_generation_and_count_flags(missing, include_count):
    bus = _Bus(plan=None) if missing else _Bus()
    response = TestClient(_app(bus=bus)).get(
        f"/v1/meal-plans/current?auto_generate=true&include_grocery_count={str(include_count).lower()}"
    )

    assert response.status_code == 200
    assert len(bus.generate_commands) == int(missing)
    assert len(bus.grocery_queries) == int(include_count)
    assert response.json()["to_buy_count"] == (0 if include_count else None)
    assert bus.current_queries[0].user_id == "user-1"
    if include_count:
        assert bus.grocery_queries[0].user_id == "user-1"
        assert bus.grocery_queries[0].plan_id == "plan-1"


def test_current_plan_defaults_preserve_generation_and_grocery_count():
    bus = _Bus(plan=None)
    response = TestClient(_app(bus=bus)).get("/v1/meal-plans/current")

    assert response.status_code == 200
    assert len(bus.generate_commands) == 1
    assert len(bus.grocery_queries) == 1
    assert response.json()["to_buy_count"] == 0


def test_current_plan_read_only_mode_does_not_generate_existing_plan():
    bus = _Bus()
    response = TestClient(_app(bus=bus)).get(
        "/v1/meal-plans/current?auto_generate=false&include_grocery_count=false"
    )
    assert response.status_code == 200
    assert bus.generate_commands == []
    assert bus.grocery_queries == []


def test_current_plan_without_auth_does_not_dispatch():
    bus = _Bus()
    response = TestClient(_app(bus=bus, authenticated=False)).get(
        "/v1/meal-plans/current"
    )
    assert response.status_code == 401
    assert bus.current_queries == []
    assert bus.generate_commands == []


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
    assert nutrition["micros_enrichment_loaded"] is False


def test_recipe_detail_marks_ai_micronutrient_values_as_estimates():
    meal = replace(
        _recipe(Micros(iron=3)),
        nutrition_micros_sources={"iron": "ai_estimate"},
        nutrition_micros_estimated=True,
    )
    response = TestClient(_app(meal)).get("/v1/recipes/catalog-1")

    assert response.status_code == 200
    nutrition = response.json()["nutrition_per_serving"]
    assert nutrition["micros"] == {"iron": 3.0}
    assert nutrition["micros_sources"] == {"iron": "ai_estimate"}
    assert nutrition["micros_estimated"] is True
    assert nutrition["micros_enrichment_loaded"] is False
    assert nutrition["score"] is None


def test_recipe_detail_reports_completed_micronutrient_enrichment():
    meal = replace(
        _recipe(Micros(iron=3)),
        nutrition_micros_sources={"iron": "usda_fdc"},
        nutrition_micros_enrichment_loaded=True,
    )
    response = TestClient(_app(meal)).get("/v1/recipes/catalog-1")

    assert response.status_code == 200
    nutrition = response.json()["nutrition_per_serving"]
    assert nutrition["micros"] == {"iron": 3.0}
    assert nutrition["micros_sources"] == {"iron": "usda_fdc"}
    assert nutrition["micros_enrichment_loaded"] is True


def test_recipe_micronutrient_enrichment_is_authenticated_and_explicit():
    bus = _Bus(recipe=_recipe())
    response = TestClient(_app(bus=bus)).post(
        "/v1/recipes/catalog-1/micronutrients/enrich"
    )

    assert response.status_code == 200
    assert bus.recipe_detail_query.enrich_micronutrients is True

    default_bus = _Bus(recipe=_recipe())
    response = TestClient(_app(bus=default_bus)).get("/v1/recipes/catalog-1")

    assert response.status_code == 200
    assert default_bus.recipe_detail_query.enrich_micronutrients is False

    public_client = TestClient(_app(recipe=_recipe(), authenticated=False))
    response = public_client.get("/v1/recipes/catalog-1")
    assert response.status_code == 200

    response = public_client.post("/v1/recipes/catalog-1/micronutrients/enrich")
    assert response.status_code == 401


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
    assert ing_response["id"] == str(deterministic_ingredient_id("Phi lê cá điêu hồng"))


def test_recipe_list_forwards_requested_meal_type():
    for meal_type in ("breakfast", "lunch", "dinner"):
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

        async def list_recipe_page(self, *, meal_type=None, **_):
            self.meal_type = meal_type
            return SimpleNamespace(items=(), total=0)

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


def test_hung_grocery_translator_returns_original_names_within_timeout():
    class _Hung:
        async def translate_texts(self, texts, source_language, target_language):
            await asyncio.sleep(5)

    item = GroceryItem(
        ingredient_id=7,
        name="Hành tím",
        category="fresh_produce",
        total_needed=2,
        unit="củ",
        stock_amount=None,
        status="needed",
        quantity_confidence="exact",
        remaining=2,
    )

    class _GroceryBus(_Bus):
        async def send(self, query):
            if query.__class__.__name__ == "GetWeeklyGroceriesQuery":
                return (_plan(), (GroceryCategory("fresh_produce", (item,)),))
            return await super().send(query)

    started = perf_counter()
    response = TestClient(_app(bus=_GroceryBus(), translation_service=_Hung())).get(
        "/v1/meal-plans/plan-1/groceries",
        headers={"Accept-Language": "en"},
    )
    elapsed = perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 3
    grocery = response.json()["categories"][0]["items"][0]
    assert grocery["name"] == "Hành tím"
    assert grocery["unit"] == "củ"


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


def test_patch_logged_slot_conflict_returns_409():
    class _ConflictBus(_Bus):
        async def send(self, query):
            if query.__class__.__name__ == "UpdateWeeklyMealPlanCommand":
                raise WeeklyMealPlanConflictError(
                    "Logged weekly meal slots cannot be replaced"
                )
            return await super().send(query)

    response = TestClient(_app(bus=_ConflictBus())).patch(
        "/v1/meal-plans/plan-1",
        headers={"Idempotency-Key": "patch-logged-slot"},
        json={
            "expected_revision": 5,
            "slots": [
                {
                    "day_index": 4,
                    "slot_index": 0,
                    "recipe_id": "3a43de9a-7e56-450d-b6ae-5a4b6dd02bfc",
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "error_code": "WEEKLY_PLAN_CONFLICT",
            "message": "Logged weekly meal slots cannot be replaced",
        }
    }


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


class _SlotBus(_Bus):
    """Answers slot state commands with the plan, slot 0-1 logged from diary."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error
        self.slot_commands = []

    async def send(self, query):
        name = query.__class__.__name__
        if name in {
            "UnlogMealPlanSlotCommand",
            "LinkMealPlanSlotCommand",
            "SetMealPlanSlotSkipCommand",
        }:
            self.slot_commands.append(query)
            if self.error is not None:
                raise self.error
            plan = _plan()
            return replace(
                plan,
                slots=tuple(
                    replace(
                        slot,
                        is_logged=True,
                        logged_meal_id="meal-1",
                        log_source="diary",
                    )
                    if slot.id == "slot-0-1"
                    else replace(slot, is_skipped=slot.id == "slot-0-2")
                    for slot in plan.slots
                ),
            )
        return await super().send(query)


def _slot_json(body, slot_id):
    return next(
        slot for day in body["plan"] for slot in day if slot["slot_id"] == slot_id
    )


def test_plan_payload_lists_features_and_slot_log_state():
    body = (
        TestClient(_app())
        .get("/v1/meal-plans/current?week_start_date=2026-09-21")
        .json()
    )

    assert body["features"] == [
        "slot_unlog",
        "slot_link",
        "slot_skip",
        "recipe_labels",
        "recipe_batch",
    ]
    slot = _slot_json(body, "slot-0-0")
    assert slot["log_source"] is None and slot["is_skipped"] is False


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("delete", "/v1/meal-plans/plan-1/slots/slot-0-1/log", None),
        ("post", "/v1/meal-plans/plan-1/slots/slot-0-1/link", {"meal_id": "m"}),
        ("post", "/v1/meal-plans/plan-1/slots/slot-0-1/skip", None),
        ("delete", "/v1/meal-plans/plan-1/slots/slot-0-1/skip", None),
    ],
)
def test_slot_state_writes_require_idempotency_key(method, path, json):
    bus = _SlotBus()
    client = TestClient(_app(bus=bus))
    kwargs = {"json": json} if json is not None else {}

    missing = getattr(client, method)(path, **kwargs)
    blank = getattr(client, method)(path, headers={"Idempotency-Key": " "}, **kwargs)

    assert missing.status_code == 422
    assert blank.status_code == 400
    assert bus.slot_commands == []


def test_unlog_returns_the_plan():
    bus = _SlotBus()

    response = TestClient(_app(bus=bus)).delete(
        "/v1/meal-plans/plan-1/slots/slot-0-1/log",
        headers={"Idempotency-Key": "unlog-1"},
    )

    assert response.status_code == 200
    (command,) = bus.slot_commands
    assert (command.plan_id, command.slot_id, command.idempotency_key) == (
        "plan-1",
        "slot-0-1",
        "unlog-1",
    )
    slot = _slot_json(response.json(), "slot-0-1")
    assert slot["log_source"] == "diary" and slot["logged_meal_id"] == "meal-1"
    assert response.json()["features"] == list(WEEKLY_PLAN_FEATURES)


@pytest.mark.parametrize(
    ("error", "status_code", "error_code"),
    [
        (
            ConflictException("not logged", error_code="WEEKLY_SLOT_NOT_LOGGED"),
            409,
            "WEEKLY_SLOT_NOT_LOGGED",
        ),
        (
            ValidationException("mismatch", error_code="WEEKLY_SLOT_MISMATCH"),
            400,
            "WEEKLY_SLOT_MISMATCH",
        ),
        (
            ResourceNotFoundException("missing", error_code="MEAL_NOT_FOUND"),
            404,
            "MEAL_NOT_FOUND",
        ),
    ],
)
def test_slot_state_errors_keep_their_codes(error, status_code, error_code):
    response = TestClient(_app(bus=_SlotBus(error))).post(
        "/v1/meal-plans/plan-1/slots/slot-0-1/link",
        headers={"Idempotency-Key": "link-1"},
        json={"meal_id": "meal-1"},
    )

    assert response.status_code == status_code
    assert response.json()["detail"]["error_code"] == error_code


def test_link_sends_meal_and_header_timezone():
    bus = _SlotBus()
    client = TestClient(_app(bus=bus))

    response = client.post(
        "/v1/meal-plans/plan-1/slots/slot-0-1/link",
        headers={"Idempotency-Key": "link-1", "X-Timezone": "Asia/Ho_Chi_Minh"},
        json={"meal_id": "meal-1"},
    )
    invalid_tz = client.post(
        "/v1/meal-plans/plan-1/slots/slot-0-1/link",
        headers={"Idempotency-Key": "link-2", "X-Timezone": "Mars/Base"},
        json={"meal_id": "meal-1"},
    )
    empty_meal = client.post(
        "/v1/meal-plans/plan-1/slots/slot-0-1/link",
        headers={"Idempotency-Key": "link-3"},
        json={"meal_id": ""},
    )

    assert response.status_code == 200 and invalid_tz.status_code == 200
    assert empty_meal.status_code == 422
    assert [(c.meal_id, c.timezone) for c in bus.slot_commands] == [
        ("meal-1", "Asia/Ho_Chi_Minh"),
        ("meal-1", None),
    ]


def test_skip_and_unskip_send_the_flag():
    bus = _SlotBus()
    client = TestClient(_app(bus=bus))

    skipped = client.post(
        "/v1/meal-plans/plan-1/slots/slot-0-2/skip",
        headers={"Idempotency-Key": "skip-1"},
    )
    unskipped = client.delete(
        "/v1/meal-plans/plan-1/slots/slot-0-2/skip",
        headers={"Idempotency-Key": "skip-2"},
    )

    assert skipped.status_code == 200 and unskipped.status_code == 200
    assert [c.skipped for c in bus.slot_commands] == [True, False]
    assert _slot_json(skipped.json(), "slot-0-2")["is_skipped"] is True


def _labelled(recipe_id, name, *, vegetarian, pork_free):
    from src.domain.model.meal_recommendation.recipe_diet_labels import (
        RecipeDietLabels,
    )

    return replace(
        _recipe(),
        id=recipe_id,
        name=name,
        browse_labels=RecipeDietLabels(vegetarian, pork_free),
    )


def test_recipe_list_labels_items_and_forwards_min_protein():
    class _ListBus(_Bus):
        async def send(self, query):
            if query.__class__.__name__ == "ListRecipesQuery":
                self.recipe_query = query
                return SimpleNamespace(
                    items=(
                        _labelled("tofu", "Tofu", vegetarian=True, pork_free=True),
                        _labelled("ga", "Gà", vegetarian=False, pork_free=True),
                    ),
                    total=2,
                )
            return await super().send(query)

    bus = _ListBus()
    client = TestClient(_app(bus=bus))

    response = client.get("/v1/recipes?min_protein=12.5")
    negative = client.get("/v1/recipes?min_protein=-1")

    assert response.status_code == 200 and negative.status_code == 422
    assert bus.recipe_query.min_protein == 12.5
    assert [
        (item["id"], item["is_vegetarian"], item["is_pork_free"])
        for item in response.json()["items"]
    ] == [("tofu", True, True), ("ga", False, True)]


def test_plan_recipe_summary_carries_diet_labels():
    plan = _plan()
    plan = replace(
        plan,
        slots=tuple(
            replace(slot, recipe_id="ga") if slot.id == "slot-0-1" else slot
            for slot in plan.slots
        ),
    )

    class _SummaryBus(_Bus):
        async def send(self, query):
            if query.__class__.__name__ == "GetRecipeSummariesQuery":
                return (_labelled("ga", "Gà", vegetarian=False, pork_free=True),)
            return await super().send(query)

    body = (
        TestClient(_app(bus=_SummaryBus(plan=plan)))
        .get("/v1/meal-plans/current?week_start_date=2026-09-21")
        .json()
    )

    recipe = _slot_json(body, "slot-0-1")["recipe"]
    assert (recipe["is_vegetarian"], recipe["is_pork_free"]) == (False, True)


class _BatchBus(_Bus):
    def __init__(self):
        super().__init__()
        self.detail_queries = []

    async def send(self, query):
        if query.__class__.__name__ == "GetRecipeDetailsQuery":
            self.detail_queries.append(query)
            return tuple(
                replace(_recipe(), id=recipe_id, name=f"Recipe {recipe_id}")
                for recipe_id in query.recipe_ids
                if recipe_id != "gone"
            )
        return await super().send(query)


def test_recipe_batch_returns_details_in_order_and_lists_missing():
    bus = _BatchBus()

    response = TestClient(_app(bus=bus)).post(
        "/v1/recipes/batch", json={"ids": ["b", "gone", "a", "b", " a "]}
    )

    assert response.status_code == 200
    assert bus.detail_queries[0].recipe_ids == ("b", "gone", "a")
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["b", "a"]
    assert body["items"][0]["nutrition_per_serving"]["protein"] == 30
    assert body["missing"] == ["gone"]


@pytest.mark.parametrize(
    "ids", [[], [str(index) for index in range(31)], [""], ["x" * 65]]
)
def test_recipe_batch_validates_ids(ids):
    bus = _BatchBus()

    response = TestClient(_app(bus=bus)).post("/v1/recipes/batch", json={"ids": ids})

    assert response.status_code == 422
    assert bus.detail_queries == []


def test_recipe_batch_requires_auth_and_is_not_a_recipe_id():
    bus = _BatchBus()

    unauthenticated = TestClient(_app(bus=bus, authenticated=False)).post(
        "/v1/recipes/batch", json={"ids": ["a"]}
    )
    detail = TestClient(_app(recipe=_recipe())).get("/v1/recipes/batch")

    assert unauthenticated.status_code in {401, 403}
    assert bus.detail_queries == []
    # GET still reads "batch" as a recipe id; only POST is the batch route.
    assert detail.status_code == 200
