from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.domain.strategies.meal_analysis_strategy import (
    AnalysisStrategyFactory,
    FoodLabelImageAnalysisStrategy,
)
from src.infra.adapters.vision_ai_service import VisionAIService

_MGR_PATCH = "src.infra.adapters.vision_ai_service.AIModelManager"


def _make_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 100, 100))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_food_label_strategy_preserves_resolution_and_passes_high_detail():
    large_image = _make_jpeg(1200, 1200)

    with patch(_MGR_PATCH) as mock_cls:
        mock_manager = MagicMock()
        mock_manager.generate_with_vision = AsyncMock(
            return_value={
                "is_food_label": True,
                "product_name": "Greek Yogurt",
                "serving_size": {"display_text": "1 cup", "grams": 227},
                "servings_per_package": 1,
                "label_calories_per_serving": 130,
                "macros_per_serving": {
                    "protein": 22,
                    "carbs": 7,
                    "fat": 0,
                },
                "confidence": 0.95,
            }
        )
        mock_cls.get_instance.return_value = mock_manager

        service = VisionAIService()
        strategy = FoodLabelImageAnalysisStrategy()

        result = await service.analyze_with_strategy(large_image, strategy)

        assert result is not None
        mock_manager.generate_with_vision.assert_called_once()
        call_kwargs = mock_manager.generate_with_vision.call_args.kwargs
        assert call_kwargs["image_detail"] == "high"
        # Bytes were not crushed down by 768px compression
        sent_bytes = call_kwargs["image_data"]
        img = Image.open(BytesIO(sent_bytes))
        assert img.width == 1200
        assert img.height == 1200


@pytest.mark.asyncio
async def test_meal_photo_strategy_compresses_to_768():
    large_image = _make_jpeg(1200, 1200)

    with patch(_MGR_PATCH) as mock_cls:
        mock_manager = MagicMock()
        mock_manager.generate_with_vision = AsyncMock(
            return_value={
                "is_food": True,
                "dish_name": "Salad Bowl",
                "foods": [
                    {
                        "name": "Lettuce",
                        "quantity_g": 100,
                        "macros": {"protein": 1, "carbs": 3, "fat": 0},
                    }
                ],
                "confidence": 0.9,
            }
        )
        mock_cls.get_instance.return_value = mock_manager

        service = VisionAIService()
        strategy = AnalysisStrategyFactory.create_basic_strategy()

        result = await service.analyze_with_strategy(large_image, strategy)

        assert result is not None
        mock_manager.generate_with_vision.assert_called_once()
        call_kwargs = mock_manager.generate_with_vision.call_args.kwargs
        sent_bytes = call_kwargs["image_data"]
        img = Image.open(BytesIO(sent_bytes))
        assert max(img.width, img.height) == 768
