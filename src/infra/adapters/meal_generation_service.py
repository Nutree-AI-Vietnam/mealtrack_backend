"""
Meal generation service implementation using AI Model Manager.
Provides resilient AI calls with automatic fallback across configured providers.
"""

import logging

from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.infra.services.ai.ai_model_manager import AIModelManager, ModelPurpose

logger = logging.getLogger(__name__)

PURPOSE_MAP = {
    "meal_names": ModelPurpose.MEAL_NAMES,
    "discovery": ModelPurpose.DISCOVERY,
    "recipe": ModelPurpose.RECIPE,
    "barcode": ModelPurpose.BARCODE,
    "parse_text": ModelPurpose.PARSE_TEXT,
    "general": ModelPurpose.GENERAL,
    "meal_plan_adjustment": ModelPurpose.MEAL_PLAN_ADJUSTMENT,
}


class MealGenerationService(MealGenerationServicePort):
    """
    Unified meal generation service using AIModelManager.
    Provides automatic fallback on provider failures.
    """

    def __init__(self):
        """Initialize with AI model manager."""
        self._ai_manager = AIModelManager.get_instance()

    async def generate_meal_plan_async(
        self,
        prompt: str,
        system_message: str,
        response_type: str = "json",
        max_tokens: int = None,
        schema: type = None,
        model_purpose: str | None = None,
        thinking_budget: int | None = None,
    ):
        """Generate meal plan — runs directly on the caller's event loop."""
        purpose = PURPOSE_MAP.get(model_purpose, ModelPurpose.GENERAL)
        return await self._ai_manager.generate(
            purpose=purpose,
            prompt=prompt,
            system_message=system_message,
            response_type=response_type,
            max_tokens=max_tokens,
            schema=schema,
        )
