"""E2E quality tests for Meal Scan, Food Label Scan, and Barcode Lookup pipelines."""

from datetime import datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_image_store,
)
from src.api.dependencies.auth import (
    get_current_user_id,
    verify_firebase_token,
    verify_firebase_uid_ownership,
)
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.main import app
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import FoodItem, Nutrition
from src.domain.strategies.meal_analysis_strategy import (
    BasicAnalysisStrategy,
    FoodLabelImageAnalysisStrategy,
)
from src.infra.adapters.vision_ai_service import VisionAIService


def _generate_realistic_food_plate_image() -> bytes:
    """Generate a realistic food plate JPEG image (800x600)."""
    img = Image.new("RGB", (800, 600), color=(245, 240, 230))
    draw = ImageDraw.Draw(img)
    # Plate
    draw.ellipse(
        (100, 50, 700, 550), fill=(255, 255, 255), outline=(200, 200, 200), width=4
    )
    # Chicken breast
    draw.ellipse(
        (200, 150, 450, 400), fill=(190, 130, 70), outline=(150, 90, 40), width=2
    )
    # Rice
    draw.ellipse(
        (420, 200, 620, 420), fill=(250, 248, 240), outline=(220, 215, 200), width=2
    )
    # Broccoli
    draw.ellipse(
        (250, 350, 400, 480), fill=(40, 140, 50), outline=(20, 100, 30), width=2
    )

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _generate_realistic_nutrition_facts_label_image() -> bytes:
    """Generate a high-res Nutrition Facts label PNG image (600x900)."""
    img = Image.new("RGB", (600, 900), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rectangle((20, 20, 580, 880), outline=(0, 0, 0), width=4)

    # Header
    draw.text((40, 30), "Nutrition Facts", fill=(0, 0, 0))
    draw.line((30, 80, 570, 80), fill=(0, 0, 0), width=10)

    # Serving size & Servings
    draw.text((40, 100), "1 serving per container", fill=(0, 0, 0))
    draw.text((40, 130), "Serving size        1 cup (227g)", fill=(0, 0, 0))
    draw.line((30, 170, 570, 170), fill=(0, 0, 0), width=6)

    # Calories
    draw.text((40, 185), "Amount Per Serving", fill=(0, 0, 0))
    draw.text((40, 215), "Calories               130", fill=(0, 0, 0))
    draw.line((30, 270, 570, 270), fill=(0, 0, 0), width=4)

    # Macros
    draw.text((40, 285), "Total Fat 0g", fill=(0, 0, 0))
    draw.line((30, 320, 570, 320), fill=(0, 0, 0), width=1)
    draw.text((40, 335), "Total Carbohydrate 7g", fill=(0, 0, 0))
    draw.line((30, 370, 570, 370), fill=(0, 0, 0), width=1)
    draw.text((40, 385), "Protein 22g", fill=(0, 0, 0))
    draw.line((30, 420, 570, 420), fill=(0, 0, 0), width=8)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _DummyImageStore:
    def get_url(self, image_id: str) -> str:
        return f"https://res.cloudinary.com/test/image/upload/v1/{image_id}.jpg"


class _MockEventBus:
    def __init__(self, handler_map):
        self._handler_map = handler_map

    async def send(self, msg):
        msg_type = type(msg)
        handler = self._handler_map.get(msg_type)
        if not handler:
            raise NotImplementedError(f"No mock handler for {msg_type}")
        return await handler(msg)


@pytest.fixture
def e2e_client():
    app.dependency_overrides[get_current_user_id] = lambda: (
        "00000000-0000-0000-0000-000000000001"
    )
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "firebase_001"}
    app.dependency_overrides[verify_firebase_uid_ownership] = (
        lambda uid="firebase_001": uid
    )
    app.dependency_overrides[get_image_store] = lambda: _DummyImageStore()

    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=None)
    app.dependency_overrides[get_async_food_reference_repository] = lambda: mock_repo

    client = TestClient(app)
    yield client

    app.dependency_overrides = {}


def test_e2e_meal_photo_upload_with_real_image(e2e_client):
    """Test full flow: generate realistic food photo, upload to endpoint, verify meal payload."""
    food_photo_bytes = _generate_realistic_food_plate_image()
    assert len(food_photo_bytes) > 5000

    async def _handle_upload_meal(cmd: UploadMealImageImmediatelyCommand):
        assert cmd.file_contents == food_photo_bytes
        assert cmd.scan_mode == "scanner"

        meal_id = str(uuid4())
        user_id = str(uuid4())
        items = [
            FoodItem(
                id=str(uuid4()),
                name="Chicken Breast",
                quantity=150.0,
                unit="g",
                macros=Macros(protein=46.5, carbs=0.0, fat=5.4),
            ),
            FoodItem(
                id=str(uuid4()),
                name="Steamed Rice",
                quantity=200.0,
                unit="g",
                macros=Macros(protein=5.4, carbs=56.4, fat=0.9),
            ),
            FoodItem(
                id=str(uuid4()),
                name="Broccoli",
                quantity=80.0,
                unit="g",
                macros=Macros(protein=2.2, carbs=5.6, fat=0.3),
            ),
        ]
        nutrition = Nutrition(
            macros=Macros(protein=54.1, carbs=62.0, fat=6.6),
            food_items=items,
        )
        meal = Meal(
            meal_id=meal_id,
            user_id=user_id,
            status=MealStatus.READY,
            created_at=datetime.utcnow(),
            ready_at=datetime.utcnow(),
            dish_name="Grilled Chicken with Rice and Broccoli",
            nutrition=nutrition,
            image=None,
        )
        return meal

    bus = _MockEventBus({UploadMealImageImmediatelyCommand: _handle_upload_meal})
    app.dependency_overrides[get_configured_event_bus] = lambda: bus

    files = {"file": ("lunch_plate.jpg", food_photo_bytes, "image/jpeg")}
    response = e2e_client.post(
        "/v1/meals/image/analyze",
        files=files,
        params={"scan_mode": "scanner"},
        headers={"Accept-Language": "en"},
    )

    if response.status_code != 200:
        print("Upload Error Details:", response.status_code, response.text)
    assert response.status_code == 200
    data = response.json()
    assert data["dish_name"] == "Grilled Chicken with Rice and Broccoli"
    assert data["status"] == "ready"
    assert len(data["food_items"]) == 3
    assert data["total_calories"] == 523.8
    assert round(data["total_nutrition"]["protein"], 1) == 54.1


def test_e2e_vision_service_detail_tier_routing_with_real_images():
    """Test VisionAIService with real generated food and label images."""
    label_bytes = _generate_realistic_nutrition_facts_label_image()
    service = VisionAIService()

    # Food label preserves resolution (does not compress to 768)
    label_compressed = service._compress_image_for_strategy(
        label_bytes, FoodLabelImageAnalysisStrategy()
    )
    assert len(label_compressed) == len(label_bytes)
    label_img = Image.open(BytesIO(label_compressed))
    assert label_img.width == 600
    assert label_img.height == 900

    # Meal photo compresses large images
    large_photo = Image.new("RGB", (2000, 1500), color=(200, 100, 50))
    buf = BytesIO()
    large_photo.save(buf, format="JPEG", quality=90)
    photo_compressed = service._compress_image_for_strategy(
        buf.getvalue(), BasicAnalysisStrategy()
    )
    compressed_img = Image.open(BytesIO(photo_compressed))
    assert max(compressed_img.size) <= 768


def test_e2e_barcode_lookup_endpoint(e2e_client):
    """Test barcode endpoint with hedged lookup query."""
    barcode = "012345678905"

    async def _handle_barcode(query: LookupBarcodeQuery):
        assert query.scanned_barcode == barcode
        return {
            "barcode": barcode,
            "name": "Organic Greek Nonfat Plain Yogurt",
            "brand": "Kirkland Signature",
            "protein_100g": 10.0,
            "carbs_100g": 3.1,
            "fat_100g": 0.0,
            "serving_size": "227g (3/4 cup)",
            "source": "fatsecret",
        }

    bus = _MockEventBus({LookupBarcodeQuery: _handle_barcode})

    from unittest.mock import patch

    with patch("src.api.routes.v1.foods.get_food_search_event_bus", return_value=bus):
        response = e2e_client.get(f"/v1/foods/barcode/{barcode}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Organic Greek Nonfat Plain Yogurt"
        assert data["protein_100g"] == 10.0
        assert data["carbs_100g"] == 3.1
        assert data["fat_100g"] == 0.0
        assert data["source"] == "fatsecret"
