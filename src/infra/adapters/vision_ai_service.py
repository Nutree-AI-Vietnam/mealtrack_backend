import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.model.ai.nutrition_contracts import (
    FoodLabelNutritionResponse,
    VisionNutritionResponse,
)
from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.services.ai_output_validation_service import (
    validate_ai_output,
)
from src.domain.strategies.meal_analysis_strategy import (
    AnalysisStrategyFactory,
    FoodLabelImageAnalysisStrategy,
    IngredientIdentificationStrategy,
    MealAnalysisStrategy,
)
from src.domain.utils.image_compression import compress_image
from src.infra.adapters.ai_json_utils import extract_json
from src.infra.config.settings import get_settings
from src.infra.services.ai.ai_model_manager import AIModelManager, ModelPurpose

logger = logging.getLogger(__name__)

MAX_URL_IMAGE_BYTES = 5 * 1024 * 1024
URL_FETCH_TIMEOUT_SECONDS = 10
ALLOWED_URL_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
VISION_VALIDATION_PURPOSE = "meal_scan"


class VisionAIService(VisionAIServicePort):
    """Vision AI service with automatic fallback on failures."""

    def __init__(self, max_output_tokens: int | None = None):
        """Initialize with AI model manager."""
        self._ai_manager = AIModelManager.get_instance()
        self._optimized_prompt_enabled = True
        self._max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else get_settings().MEAL_ANALYZE_MAX_OUTPUT_TOKENS
        )

    def _compress_image(self, image_bytes: bytes) -> bytes:
        """Fallback compression and format normalization using shared utility."""
        return compress_image(image_bytes)

    def _compress_image_for_strategy(
        self, image_bytes: bytes, strategy: MealAnalysisStrategy
    ) -> bytes:
        """Compress image preserving food label crop resolution when applicable."""
        if isinstance(strategy, FoodLabelImageAnalysisStrategy):
            if len(image_bytes) <= 2 * 1024 * 1024:
                return image_bytes
            return compress_image(image_bytes, max_dim=1600)
        return self._compress_image(image_bytes)

    async def analyze_with_strategy(
        self, image_bytes: bytes, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """
        Analyze a food image using the provided analysis strategy with automatic fallback.

        Args:
            image_bytes: The raw bytes of the image to analyze
            strategy: The analysis strategy to use

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        image_bytes = self._compress_image_for_strategy(image_bytes, strategy)

        if isinstance(strategy, IngredientIdentificationStrategy):
            return await self._analyze_without_nutrition_contract(image_bytes, strategy)

        try:
            is_food_label = isinstance(strategy, FoodLabelImageAnalysisStrategy)
            schema = (
                FoodLabelNutritionResponse if is_food_label else VisionNutritionResponse
            )
            validation_purpose = (
                "food_label_scan" if is_food_label else VISION_VALIDATION_PURPOSE
            )
            model_purpose = (
                ModelPurpose.FOOD_LABEL_SCAN
                if is_food_label
                else ModelPurpose.MEAL_SCAN
            )
            settings = get_settings()
            detail_tier = (
                settings.AI_FOOD_LABEL_DETAIL_TIER
                if is_food_label
                else settings.AI_MEAL_SCAN_DETAIL_TIER
            )
            result = await self._ai_manager.generate_with_vision(
                purpose=model_purpose,
                prompt=strategy.get_user_message(),
                image_data=image_bytes,
                system_message=strategy.get_analysis_prompt(),
                max_tokens=self._max_output_tokens,
                schema=schema,
                image_detail=detail_tier,
            )
            structured_data = validate_ai_output(
                result,
                schema=schema,
                purpose=validation_purpose,
                attempt_count=1,
            )
            return {
                "raw_response": json.dumps(structured_data),
                "structured_data": structured_data,
                "strategy_used": strategy.get_strategy_name(),
            }
        except AIOutputValidationError as exc:
            logger.warning(
                "[AI-OUTPUT-VALIDATION-FAILED] purpose=%s strategy=%s attempt=%s "
                "details=%s",
                validation_purpose,
                strategy.get_strategy_name(),
                exc.attempt_count,
                exc.validation_details,
            )
            raise
        except AIUnavailableError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Failed to analyze image with {strategy.get_strategy_name()}: {str(e)}"
            ) from e

    async def _analyze_without_nutrition_contract(
        self, image_bytes: bytes, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """Run non-nutrition vision strategies with their own response contract."""
        try:
            result = await self._ai_manager.generate_with_vision(
                purpose=ModelPurpose.MEAL_SCAN,
                prompt=strategy.get_user_message(),
                image_data=image_bytes,
                system_message=strategy.get_analysis_prompt(),
                max_tokens=self._max_output_tokens,
            )
            structured_data = (
                result if isinstance(result, dict) else extract_json(str(result))
            )
            return {
                "raw_response": json.dumps(structured_data),
                "structured_data": structured_data,
                "strategy_used": strategy.get_strategy_name(),
            }
        except AIUnavailableError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Failed to analyze image with {strategy.get_strategy_name()}: {str(e)}"
            ) from e

    async def analyze_by_url_with_strategy(
        self, image_url: str, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """
        Analyze a food image by public URL using the provided analysis strategy.

        Args:
            image_url: Public URL of the image to analyze
            strategy: The analysis strategy to use

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        image_bytes = await self._fetch_image_url(image_url)
        return await self.analyze_with_strategy(image_bytes, strategy)

    async def _fetch_image_url(self, image_url: str) -> bytes:
        """Fetch bounded image bytes from an HTTP(S) URL."""
        parsed = urlparse(image_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Image URL must be an absolute HTTP(S) URL")

        def _fetch() -> bytes:
            request = Request(image_url, headers={"User-Agent": "MealTrack/1.0"})
            with urlopen(request, timeout=URL_FETCH_TIMEOUT_SECONDS) as response:
                content_type = response.headers.get_content_type()
                if content_type not in ALLOWED_URL_IMAGE_CONTENT_TYPES:
                    raise ValueError(
                        f"Unsupported image URL content type: {content_type}"
                    )
                image_bytes = response.read(MAX_URL_IMAGE_BYTES + 1)
                if len(image_bytes) > MAX_URL_IMAGE_BYTES:
                    raise ValueError("Image URL content too large (max 5MB)")
                return image_bytes

        try:
            return await asyncio.to_thread(_fetch)
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to fetch image URL: {str(e)}") from e

    async def analyze(
        self,
        image_bytes: bytes,
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Analyze a food image to extract nutritional information.

        Args:
            image_bytes: The raw bytes of the image to analyze

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        strategy = AnalysisStrategyFactory.create_basic_strategy(
            optimized_prompt_enabled=self._optimized_prompt_enabled,
            language=language,
        )
        return await self.analyze_with_strategy(image_bytes, strategy)

    async def analyze_with_portion_context(
        self, image_bytes: bytes, portion_size: float, unit: str
    ) -> dict[str, Any]:
        """
        Analyze a food image with specific portion size context.

        Args:
            image_bytes: The raw bytes of the image to analyze
            portion_size: The target portion size
            unit: The unit of the portion size

        Returns:
            JSON-compatible dictionary with the raw AI response
        """
        strategy = AnalysisStrategyFactory.create_portion_strategy(portion_size, unit)
        return await self.analyze_with_strategy(image_bytes, strategy)

    async def analyze_with_ingredients_context(
        self, image_bytes: bytes, ingredients: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Analyze a food image with known ingredients context.

        Args:
            image_bytes: The raw bytes of the image to analyze
            ingredients: List of ingredient dictionaries with name, quantity, unit

        Returns:
            JSON-compatible dictionary with the raw AI response
        """
        strategy = AnalysisStrategyFactory.create_ingredient_strategy(ingredients)
        return await self.analyze_with_strategy(image_bytes, strategy)

    async def analyze_with_weight_context(
        self, image_bytes: bytes, weight_grams: float
    ) -> dict[str, Any]:
        """
        Analyze a food image with specific weight context for accurate nutrition.

        Args:
            image_bytes: The raw bytes of the image to analyze
            weight_grams: The target weight in grams

        Returns:
            JSON-compatible dictionary with the raw AI response
        """
        strategy = AnalysisStrategyFactory.create_weight_strategy(weight_grams)
        return await self.analyze_with_strategy(image_bytes, strategy)
