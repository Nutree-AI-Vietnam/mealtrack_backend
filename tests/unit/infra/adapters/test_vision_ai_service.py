from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.domain.services.meal_analysis.fast_path_policy import (
    MEAL_ANALYZE_DEFAULT_MAX_OUTPUT_TOKENS,
)
from src.infra.adapters.vision_ai_service import VisionAIService

_MGR_PATCH = "src.infra.adapters.vision_ai_service.AIModelManager"


def _make_jpeg(width: int, height: int, quality: int = 95) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _valid_vision_response() -> dict:
    return {
        "dish_name": "test",
        "foods": [
            {
                "name": "rice",
                "quantity_g": 180,
                "macros": {"protein": 4, "carbs": 50, "fat": 1},
            }
        ],
        "confidence": 0.8,
    }


def _make_service(
    max_output_tokens: int | None = None,
    response: dict | None = None,
) -> VisionAIService:
    with patch(_MGR_PATCH) as mock_cls:
        mock_manager = MagicMock()
        mock_manager.generate_with_vision = AsyncMock(
            return_value=response or _valid_vision_response()
        )
        mock_cls.get_instance.return_value = mock_manager
        return VisionAIService(max_output_tokens=max_output_tokens)


def test_vision_service_uses_ai_model_manager():
    with patch(_MGR_PATCH) as mock_cls:
        mock_manager = MagicMock()
        mock_manager.generate_with_vision = AsyncMock(return_value={})
        mock_cls.get_instance.return_value = mock_manager

        VisionAIService()

        mock_cls.get_instance.assert_called_once()


def test_compress_image_resizes_large_image():
    service = _make_service()
    large_bytes = _make_jpeg(4000, 3000)

    result = service._compress_image(large_bytes)

    img = Image.open(BytesIO(result))
    assert max(img.size) == 2048


def test_compress_image_skips_small_image():
    service = _make_service()
    # Fast-path only applies to small JPEGs — PNG/WebP always get converted
    small_bytes = _make_jpeg(400, 300, quality=50)
    assert len(small_bytes) < 200 * 1024  # confirm precondition

    result = service._compress_image(small_bytes)

    assert result is small_bytes  # skip path returns original object unchanged


def test_compress_image_fallback_on_corrupt_bytes():
    service = _make_service()
    corrupt = b"not an image at all"

    result = service._compress_image(corrupt)

    assert result == corrupt


def test_extract_json_failure_does_not_log_raw_ai_response(caplog):
    from src.infra.ai.json_extract import extract_json

    raw_response = "not json with private meal notes and user@example.com"

    with caplog.at_level("WARNING"), pytest.raises(ValueError):
        extract_json(raw_response)

    assert raw_response not in caplog.text
    assert "user@example.com" not in caplog.text
    assert "content_len=" in caplog.text


@pytest.mark.asyncio
async def test_analyze_with_strategy_compresses_before_sending():
    service = _make_service()

    large_bytes = _make_jpeg(4000, 3000)

    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()

    await service.analyze_with_strategy(large_bytes, strategy)

    # Verify vision() was called with compressed image bytes
    call_kwargs = service._ai_manager.generate_with_vision.call_args
    image_data = call_kwargs.kwargs["image_data"]

    # The image should be compressed (smaller than 4000x3000)
    img = Image.open(BytesIO(image_data))
    assert max(img.size) == 2048


@pytest.mark.asyncio
async def test_analyze_with_strategy_passes_meal_analyze_max_tokens():
    """Vision calls need enough output budget for full meal-analysis JSON."""
    service = _make_service()
    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()

    image = _make_jpeg(400, 300)
    await service.analyze_with_strategy(image, strategy)

    call_kwargs = service._ai_manager.generate_with_vision.call_args.kwargs
    assert call_kwargs.get("max_tokens") == MEAL_ANALYZE_DEFAULT_MAX_OUTPUT_TOKENS


@pytest.mark.asyncio
async def test_analyze_with_strategy_allows_max_token_override():
    service = _make_service(max_output_tokens=4096)
    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()

    image = _make_jpeg(400, 300)
    await service.analyze_with_strategy(image, strategy)

    call_kwargs = service._ai_manager.generate_with_vision.call_args.kwargs
    assert call_kwargs.get("max_tokens") == 4096


@pytest.mark.asyncio
async def test_analyze_by_url_passes_meal_analyze_max_tokens():
    """URL analysis must fetch bytes and pass the meal-analysis token budget."""
    service = _make_service()
    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()
    image_bytes = _make_jpeg(400, 300)

    mock_response = MagicMock()
    mock_response.headers.get_content_type.return_value = "image/jpeg"
    mock_response.read.return_value = image_bytes
    mock_response.__enter__.return_value = mock_response
    with patch(
        "src.infra.adapters.vision_ai_service.urlopen",
        return_value=mock_response,
    ):
        await service.analyze_by_url_with_strategy(
            "http://example.com/food.jpg", strategy
        )

    call_kwargs = service._ai_manager.generate_with_vision.call_args.kwargs
    assert call_kwargs.get("max_tokens") == MEAL_ANALYZE_DEFAULT_MAX_OUTPUT_TOKENS
    assert call_kwargs["image_data"] == image_bytes


@pytest.mark.asyncio
async def test_analyze_by_url_rejects_non_http_url():
    service = _make_service()
    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()

    with pytest.raises(ValueError, match="HTTP"):
        await service.analyze_by_url_with_strategy("file:///tmp/food.jpg", strategy)


@pytest.mark.asyncio
async def test_analyze_by_url_rejects_non_image_content_type():
    service = _make_service()
    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    strategy = AnalysisStrategyFactory.create_basic_strategy()

    mock_response = MagicMock()
    mock_response.headers.get_content_type.return_value = "text/html"
    mock_response.__enter__.return_value = mock_response

    with (
        patch(
            "src.infra.adapters.vision_ai_service.urlopen",
            return_value=mock_response,
        ),
        pytest.raises(ValueError, match="content type"),
    ):
        await service.analyze_by_url_with_strategy("https://example.com/page", strategy)


def test_recipe_token_limit_is_1200():
    """PARALLEL_SINGLE_MEAL_TOKENS must be 1200, not 4000."""
    from src.domain.services.meal_suggestion.recipe_attempt_builder import (
        PARALLEL_SINGLE_MEAL_TOKENS,
    )

    assert PARALLEL_SINGLE_MEAL_TOKENS == 1200


@pytest.mark.asyncio
async def test_scan_provenance_is_stored_without_changing_model_output():
    import hashlib
    import json

    from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory

    service = _make_service()
    strategy = AnalysisStrategyFactory.create_basic_strategy()
    result = await service.analyze_with_strategy(_make_jpeg(400, 300), strategy)
    kwargs = service._ai_manager.generate_with_vision.call_args.kwargs
    raw = json.loads(result["raw_response"])
    assert (
        raw["_scan_provenance"]["image_sha256"]
        == hashlib.sha256(kwargs["image_data"]).hexdigest()
    )
    assert (
        raw["_scan_provenance"]["prompt_sha256"]
        == hashlib.sha256(strategy.get_analysis_prompt().encode()).hexdigest()
    )
    assert "_scan_provenance" not in result["structured_data"]
