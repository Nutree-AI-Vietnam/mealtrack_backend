import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # Ensure predictable environment (don’t mount uploads)
    monkeypatch.setenv("ENVIRONMENT", "test")
    # Import a fresh app module instance and patch lifespan side-effects.
    # Other tests may import `src.api.main` with different ENVIRONMENT values.
    import importlib
    import sys

    sys.modules.pop("src.api.main", None)
    main = importlib.import_module("src.api.main")

    main.initialize_firebase = lambda: None  # type: ignore[assignment]

    async def _noop_async(*args, **kwargs):
        return None

    main.initialize_cache_layer = _noop_async  # type: ignore[assignment]
    main.shutdown_cache_layer = _noop_async  # type: ignore[assignment]

    # Dependency overrides to avoid DB/event bus initialization
    from src.api.base_dependencies import get_image_store
    from src.api.dependencies.auth import get_current_user_id, verify_firebase_token
    from src.api.dependencies.event_bus import get_configured_event_bus

    class _DummyBus:
        async def send(self, msg):
            raise AssertionError(
                "event_bus.send should not be called in these smoke tests"
            )

    class _DummyImageStore:
        def get_url(self, image_id: str) -> str:
            return f"https://example.com/{image_id}"

        async def generate_upload_signature_async(
            self, image_id: str, ttl: int = 300
        ) -> dict:
            return {
                "image_id": image_id,
                "cloud_name": "test_cloud",
                "api_key": "test_key",
                "timestamp": 1700000000,
                "signature": "test_sig",
                "folder": "mealtrack",
                "public_id": f"mealtrack/{image_id}",
            }

    main.app.dependency_overrides[get_current_user_id] = lambda: "user_1"
    main.app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "user_1"}
    main.app.dependency_overrides[get_configured_event_bus] = lambda: _DummyBus()
    main.app.dependency_overrides[get_image_store] = lambda: _DummyImageStore()

    with TestClient(main.app) as c:
        yield c

    main.app.dependency_overrides = {}


def test_openapi_json_available(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "MealTrack API"


def test_meals_analyze_rejects_invalid_file_type(client: TestClient):
    r = client.post(
        "/v1/meals/image/analyze",
        files={"file": ("x.gif", b"abc", "image/gif")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "INVALID_FILE_TYPE"


def test_meals_analyze_rejects_food_label_mode(client: TestClient):
    r = client.post(
        "/v1/meals/image/analyze?scan_mode=food_label",
        files={"file": ("x.jpg", b"abc", "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "INVALID_SCAN_MODE"


def test_meals_analyze_rejects_invalid_target_date(client: TestClient):
    r = client.post(
        "/v1/meals/image/analyze?target_date=2024-99-99",
        files={"file": ("x.jpg", b"abc", "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "INVALID_DATE_FORMAT"


def test_meals_analyze_rejects_too_large(monkeypatch, client: TestClient):
    # Avoid allocating 10MB in tests by shrinking limit
    import src.api.routes.v1.meals_analyze as meals_analyze_routes

    monkeypatch.setattr(meals_analyze_routes, "MAX_FILE_SIZE", 3)

    r = client.post(
        "/v1/meals/image/analyze",
        files={"file": ("x.jpg", b"abcd", "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "FILE_SIZE_EXCEEDS_MAXIMUM"


def test_meals_analyze_ai_unavailable_returns_503(client: TestClient):
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.exceptions.ai_exceptions import AIUnavailableError

    class _UnavailableBus:
        async def send(self, msg):
            raise AIUnavailableError(
                "All vision models failed for meal_scan",
                attempted_models=["gpt-5.4-mini-2026-03-17", "gpt-5.4-mini-2026-03-17"],
                last_error="503 UNAVAILABLE",
            )

    client.app.dependency_overrides[get_configured_event_bus] = (
        lambda: _UnavailableBus()
    )

    r = client.post(
        "/v1/meals/image/analyze",
        files={"file": ("x.jpg", b"image-bytes", "image/jpeg")},
    )

    assert r.status_code == 503
    assert r.json()["detail"]["error_code"] == "AI_UNAVAILABLE"


def test_meals_analyze_localization_error_returns_controlled_422(client: TestClient):
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError

    class _LocalizationErrorBus:
        async def send(self, msg):
            raise MealResponseLocalizationError("localized food item is missing")

    client.app.dependency_overrides[get_configured_event_bus] = (
        lambda: _LocalizationErrorBus()
    )

    r = client.post(
        "/v1/meals/image/analyze",
        files={"file": ("x.jpg", b"image-bytes", "image/jpeg")},
        headers={"Accept-Language": "vi"},
    )

    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "AI_OUTPUT_INVALID"


def test_meals_analyze_non_food_returns_not_food_image(client: TestClient):
    from src.api.dependencies.event_bus import get_configured_event_bus

    class _NonFoodBus:
        async def send(self, msg):
            raise ValueError("Image does not appear to contain food")

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _NonFoodBus()

    r = client.post(
        "/v1/meals/image/analyze",
        files={"file": ("x.jpg", b"image-bytes", "image/jpeg")},
    )

    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "NOT_FOOD_IMAGE"


def test_scan_by_url_non_food_returns_not_food_image(client: TestClient):
    from src.api.dependencies.event_bus import get_configured_event_bus

    class _NonFoodBus:
        async def send(self, msg):
            raise ValueError("Image does not appear to contain food")

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _NonFoodBus()

    payload = {
        "image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        "image_id": "abc",
    }
    r = client.post("/v1/meals/scan-by-url", json=payload)

    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "NOT_FOOD_IMAGE"


def test_scan_by_url_localization_error_returns_controlled_422(client: TestClient):
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError

    class _LocalizationErrorBus:
        async def send(self, msg):
            raise MealResponseLocalizationError("localized food item is missing")

    client.app.dependency_overrides[get_configured_event_bus] = (
        lambda: _LocalizationErrorBus()
    )

    r = client.post(
        "/v1/meals/scan-by-url",
        json={
            "image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
            "image_id": "abc",
        },
        headers={"Accept-Language": "vi"},
    )

    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "AI_OUTPUT_INVALID"


def test_scan_by_url_rejects_food_label_mode(client: TestClient):
    payload = {
        "image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        "image_id": "abc",
        "scan_mode": "food_label",
    }
    r = client.post("/v1/meals/scan-by-url", json=payload)

    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "INVALID_SCAN_MODE"


def test_food_label_scan_by_url_uses_food_label_mode(client: TestClient):
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.model.meal import Meal, MealImage, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    class _FoodLabelBus:
        async def send(self, msg):
            assert msg.scan_mode == "food_label"
            assert (
                msg.label_crop_image_url
                == "https://res.cloudinary.com/test/image/upload/v123/mealtrack/crop.jpg"
            )
            assert msg.label_crop_public_id == "mealtrack/crop"
            assert msg.crop_metadata == {"crop_strategy": "food_label_visible_frame_v1"}
            return Meal(
                meal_id=str(uuid4()),
                user_id=str(uuid4()),
                status=MealStatus.READY,
                created_at=datetime(2026, 7, 2, 12, 0),
                ready_at=datetime(2026, 7, 2, 12, 0),
                image=MealImage(
                    image_id=str(uuid4()),
                    format="jpeg",
                    size_bytes=3,
                    url=msg.image_url,
                ),
                dish_name="Unnamed Food",
                source="food_label",
                food_label_metadata={
                    "product_name": "Protein Bar",
                    "serving_size": {"display_text": "1 bar (55g)", "grams": 55},
                    "servings_per_package": 8,
                    "confidence": 0.92,
                },
                nutrition=Nutrition(
                    macros=Macros(protein=3, carbs=37, fat=8),
                    food_items=[
                        FoodItem(
                            id="label-item",
                            name="Protein Bar",
                            quantity=55,
                            unit="g",
                            macros=Macros(protein=3, carbs=37, fat=8),
                        )
                    ],
                ),
            )

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _FoodLabelBus()

    payload = {
        "image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        "image_id": "abc",
        "label_crop_image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/crop.jpg",
        "label_crop_image_id": "crop",
        "crop_metadata": {"crop_strategy": "food_label_visible_frame_v1"},
    }
    r = client.post("/v1/meals/food-label/scan-by-url", json=payload)

    assert r.status_code == 200
    assert r.json()["source"] == "food_label"
    assert r.json()["food_label_metadata"]["servings_per_package"] == 8


def test_food_label_scan_by_url_rejects_partial_crop_payload(client: TestClient):
    payload = {
        "image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        "image_id": "abc",
        "label_crop_image_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/crop.jpg",
    }
    r = client.post("/v1/meals/food-label/scan-by-url", json=payload)

    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "INVALID_LABEL_CROP_IMAGE"


def test_upload_token_returns_signed_params(client: TestClient):
    r = client.get("/v1/meals/upload-token")
    assert r.status_code == 200
    data = r.json()
    assert "image_id" in data
    assert "cloud_name" in data
    assert "signature" in data
    assert "timestamp" in data
    assert data["folder"] == "mealtrack"
    assert data["public_id"].startswith("mealtrack/")


def test_user_profiles_onboarding_invalid_birth_date(client: TestClient):
    payload = {
        "birth_year": 2000,
        "birth_month": 2,
        "birth_day": 31,  # invalid
        "gender": "male",
        "height": 170,
        "weight": 70,
        "body_fat_percentage": None,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 30,
        "goal": "cut",
        "pain_points": [],
        "dietary_preferences": [],
        "meals_per_day": 3,
        "referral_sources": [],
    }
    r = client.post("/v1/user-profiles/", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid birth date"


def test_meals_manual_create_happy_path(client: TestClient):
    """Smoke coverage for POST /v1/meals/manual (manual+text group, happy path)."""
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.model.meal import Meal, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    class _ManualCreateBus:
        async def send(self, msg):
            return Meal(
                meal_id=str(uuid4()),
                user_id=str(uuid4()),
                status=MealStatus.READY,
                created_at=datetime(2026, 7, 2, 12, 0),
                ready_at=datetime(2026, 7, 2, 12, 0),
                image=None,
                dish_name="Chicken Rice",
                source="manual",
                nutrition=Nutrition(
                    macros=Macros(protein=30, carbs=50, fat=10),
                    food_items=[
                        FoodItem(
                            id="item-1",
                            name="Chicken",
                            quantity=150,
                            unit="g",
                            macros=Macros(protein=30, carbs=50, fat=10),
                        )
                    ],
                ),
            )

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _ManualCreateBus()

    payload = {
        "dish_name": "Chicken Rice",
        "items": [{"fdc_id": 12345, "quantity": 150, "unit": "g"}],
        "meal_type": "lunch",
        "source": "manual",
    }
    r = client.post("/v1/meals/manual", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["meal_detail"]["dish_name"] == "Chicken Rice"


def test_meals_get_by_id_read_group(client: TestClient):
    """Smoke coverage for GET /v1/meals/{meal_id} (read group)."""
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.model.meal import Meal, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    meal_id = str(uuid4())
    meal = Meal(
        meal_id=meal_id,
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2026, 7, 2, 12, 0),
        ready_at=datetime(2026, 7, 2, 12, 0),
        image=None,
        dish_name="Grilled Salmon",
        source="scanner",
        nutrition=Nutrition(
            macros=Macros(protein=35, carbs=5, fat=20),
            food_items=[
                FoodItem(
                    id="item-1",
                    name="Salmon",
                    quantity=200,
                    unit="g",
                    macros=Macros(protein=35, carbs=5, fat=20),
                )
            ],
        ),
    )

    class _GetMealBus:
        async def send(self, msg):
            return meal

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _GetMealBus()

    r = client.get(f"/v1/meals/{meal_id}")

    assert r.status_code == 200, r.text
    assert r.json()["dish_name"] == "Grilled Salmon"
    assert r.json()["meal_id"] == meal_id


def test_meals_edit_ingredients_edit_group(client: TestClient):
    """Smoke coverage for PUT /v1/meals/{meal_id}/ingredients (edit group)."""
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.app.commands.meal import EditMealCommand
    from src.domain.model.meal import Meal, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    meal_id = str(uuid4())
    meal = Meal(
        meal_id=meal_id,
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2026, 7, 2, 12, 0),
        ready_at=datetime(2026, 7, 2, 12, 0),
        image=None,
        dish_name="Updated Dish",
        source="scanner",
        nutrition=Nutrition(
            macros=Macros(protein=35, carbs=5, fat=20),
            food_items=[
                FoodItem(
                    id="item-1",
                    name="Salmon",
                    quantity=200,
                    unit="g",
                    macros=Macros(protein=35, carbs=5, fat=20),
                )
            ],
        ),
    )
    sent = []

    class _EditBus:
        async def send(self, msg):
            sent.append(msg)
            if isinstance(msg, EditMealCommand):
                return {"success": True}
            return meal

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _EditBus()

    r = client.put(
        f"/v1/meals/{meal_id}/ingredients",
        json={"dish_name": "Updated Dish", "food_item_changes": []},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"success": True}
    edit_command = next(m for m in sent if isinstance(m, EditMealCommand))
    assert edit_command.meal_id == meal_id
    assert edit_command.dish_name == "Updated Dish"


def test_meals_edit_ingredients_v2_includes_meal_detail(client: TestClient):
    """v2 PUT returns meal_detail so clients can skip a follow-up GET."""
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.app.commands.meal import EditMealCommand
    from src.domain.model.meal import Meal, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    meal_id = str(uuid4())
    meal = Meal(
        meal_id=meal_id,
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2026, 7, 2, 12, 0),
        ready_at=datetime(2026, 7, 2, 12, 0),
        image=None,
        dish_name="Updated Dish",
        source="scanner",
        nutrition=Nutrition(
            macros=Macros(protein=35, carbs=5, fat=20),
            food_items=[
                FoodItem(
                    id="item-1",
                    name="Salmon",
                    quantity=200,
                    unit="g",
                    macros=Macros(protein=35, carbs=5, fat=20),
                )
            ],
        ),
    )

    sent = []

    class _EditBus:
        async def send(self, msg):
            sent.append(msg)
            if isinstance(msg, EditMealCommand):
                return {"success": True}
            return meal

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _EditBus()

    r = client.put(
        f"/v1/meals/{meal_id}/ingredients",
        json={
            "dish_name": "Updated Dish",
            "food_item_changes": [],
            "nutrition_contract_version": 2,
            "nutrition_override": {
                "calories": 500,
                "protein": 20,
                "carbs": 30,
                "fat": 15,
            },
        },
        headers={
            "X-Nutrition-Contract-Version": "2",
            "X-App-Version": "1.0.0",
            "X-Platform": "ios",
            "Idempotency-Key": "smoke-edit-v2",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["meal_detail"]["meal_id"] == meal_id
    assert body["meal_detail"]["dish_name"] == "Updated Dish"
    assert body["meal_detail"]["food_items"][0]["name"] == "Salmon"
    edit_command = next(m for m in sent if isinstance(m, EditMealCommand))
    assert edit_command.override_intent == "user_entered"


def test_meals_edit_ingredients_v2_meal_detail_keeps_requested_locale(
    client: TestClient,
):
    """PUT meal_detail must use Accept-Language the same way GET does."""
    import json
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.app.commands.meal import EditMealCommand
    from src.domain.model.meal import FoodItemTranslation, Meal, MealStatus, MealTranslation
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    meal_id = str(uuid4())
    food_id = str(uuid4())
    meal = Meal(
        meal_id=meal_id,
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2026, 8, 22, 12, 0),
        ready_at=datetime(2026, 8, 22, 12, 0),
        image=None,
        dish_name="Broken rice with grilled pork chop",
        source="scanner",
        raw_gpt_json=json.dumps(
            {
                "dish_name": "Broken rice with grilled pork chop",
                "foods": [{"name": "Broken Rice"}],
            }
        ),
        nutrition=Nutrition(
            macros=Macros(protein=35, carbs=45, fat=20),
            food_items=[
                FoodItem(
                    id=food_id,
                    name="Broken Rice",
                    quantity=200,
                    unit="g",
                    macros=Macros(protein=8, carbs=45, fat=1),
                )
            ],
        ),
        translations={
            "vi": MealTranslation(
                meal_id=meal_id,
                language="vi",
                dish_name="Cơm tấm sườn, bì, chả",
                meal_ingredients=["Cơm tấm"],
                food_items=[
                    FoodItemTranslation(food_item_id=food_id, name="Cơm tấm"),
                ],
            )
        },
    )

    sent = []

    class _EditBus:
        async def send(self, msg):
            sent.append(msg)
            if isinstance(msg, EditMealCommand):
                return {"success": True}
            return meal

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _EditBus()

    r = client.put(
        f"/v1/meals/{meal_id}/ingredients",
        json={
            "food_item_changes": [],
            "nutrition_contract_version": 2,
            "nutrition_override": {
                "calories": 500,
                "protein": 20,
                "carbs": 30,
                "fat": 15,
            },
        },
        headers={
            "Accept-Language": "vi",
            "X-Nutrition-Contract-Version": "2",
            "X-App-Version": "1.0.0",
            "X-Platform": "ios",
            "Idempotency-Key": "smoke-edit-v2-locale",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()["meal_detail"]
    assert body["dish_name"] == "Cơm tấm sườn, bì, chả"
    assert body["food_items"][0]["name"] == "Cơm tấm"
    assert body["food_items"][0]["canonical_name"] == "Broken Rice"
    assert body["translation_language"] == "vi"


def test_meals_streak_smoke(client: TestClient):
    """Smoke coverage for GET /v1/meals/streak."""
    from src.api.dependencies.event_bus import get_configured_event_bus

    class _StreakBus:
        async def send(self, msg):
            return {
                "current_streak": 5,
                "best_streak": 10,
                "last_logged_date": "2026-07-02",
                "scan_count": 3,
            }

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _StreakBus()

    r = client.get("/v1/meals/streak")

    assert r.status_code == 200, r.text
    assert r.json()["current_streak"] == 5
    assert r.json()["best_streak"] == 10


def test_meals_daily_breakdown_smoke(client: TestClient):
    """Smoke coverage for GET /v1/meals/weekly/daily-breakdown."""
    from src.api.dependencies.event_bus import get_configured_event_bus

    day = {
        "date": "2026-07-02",
        "calories_consumed": 1800.0,
        "calories_target": 2000.0,
        "protein_consumed": 120.0,
        "protein_target": 150.0,
        "carbs_consumed": 180.0,
        "carbs_target": 200.0,
        "fat_consumed": 60.0,
        "fat_target": 70.0,
        "meal_count": 3,
    }

    class _BreakdownBus:
        async def send(self, msg):
            return {"days": [day], "week_start": "2026-06-30"}

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BreakdownBus()

    r = client.get("/v1/meals/weekly/daily-breakdown")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["week_start"] == "2026-06-30"
    assert len(body["days"]) == 1


def test_meals_weekly_budget_smoke(client: TestClient):
    """Smoke coverage for GET /v1/meals/weekly/budget."""
    from src.api.dependencies.event_bus import get_configured_event_bus

    class _BudgetBus:
        async def send(self, msg):
            return {
                "week_start_date": "2026-06-30",
                "target_calories": 14000.0,
                "target_protein": 700.0,
                "target_carbs": 1750.0,
                "target_fat": 466.7,
                "consumed_calories": 2100.0,
                "consumed_protein": 100.0,
                "consumed_carbs": 250.0,
                "consumed_fat": 100.0,
                "remaining_calories": 11900.0,
                "remaining_protein": 600.0,
                "remaining_carbs": 1500.0,
                "remaining_fat": 366.7,
                "adjusted_daily_calories": 2000.0,
                "adjusted_daily_carbs": 250.0,
                "adjusted_daily_fat": 66.7,
                "daily_protein": 100.0,
                "remaining_days": 6,
                "bmr_floor_active": False,
                "cheat_days": [],
            }

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _BudgetBus()

    r = client.get("/v1/meals/weekly/budget")

    assert r.status_code == 200, r.text
    assert r.json()["target_calories"] == 14000.0


def test_meals_value_insights_unavailable_without_cache_service(client: TestClient):
    """Smoke coverage for GET /v1/meals/{meal_id}/value-insights.

    Documents CURRENT behavior: with no cache_service configured (default in
    TestClient/test env), the endpoint returns status="unavailable" rather
    than attempting AI generation.
    """
    from datetime import datetime
    from uuid import uuid4

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.domain.model.meal import Meal, MealStatus
    from src.domain.model.nutrition import FoodItem, Macros, Nutrition

    meal_id = str(uuid4())
    meal = Meal(
        meal_id=meal_id,
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2026, 7, 2, 12, 0),
        ready_at=datetime(2026, 7, 2, 12, 0),
        image=None,
        dish_name="Grilled Salmon",
        source="scanner",
        nutrition=Nutrition(
            macros=Macros(protein=35, carbs=5, fat=20),
            food_items=[
                FoodItem(
                    id="item-1",
                    name="Salmon",
                    quantity=200,
                    unit="g",
                    macros=Macros(protein=35, carbs=5, fat=20),
                )
            ],
        ),
    )

    class _ValueInsightsBus:
        async def send(self, msg):
            return meal

    client.app.dependency_overrides[get_configured_event_bus] = lambda: _ValueInsightsBus()

    r = client.get(f"/v1/meals/{meal_id}/value-insights")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["value_insights"] is None


def test_users_sync_forbidden_when_token_uid_mismatch(client: TestClient):
    # Override token to mismatch the request.firebase_uid
    import src.api.main as main
    from src.api.dependencies.auth import verify_firebase_token

    main.app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "uid_token"}

    payload = {
        "firebase_uid": "uid_request",
        "email": "a@b.com",
        "phone_number": None,
        "display_name": None,
        "photo_url": None,
        "provider": "google",
        "username": None,
        "first_name": None,
        "last_name": None,
    }
    r = client.post("/v1/users/sync", json=payload)
    assert r.status_code == 403
