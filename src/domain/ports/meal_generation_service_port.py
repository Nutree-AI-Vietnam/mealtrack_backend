"""
Port for meal generation services following clean architecture.
Single LLM service that handles different prompts and request data.
"""

from abc import ABC, abstractmethod
from typing import Any


class MealGenerationServicePort(ABC):
    """Unified port for all meal generation using single LLM with different prompts."""

    @abstractmethod
    async def generate_meal_plan_async(
        self,
        prompt: str,
        system_message: str,
        response_type: str = "json",
        max_tokens: int | None = None,
        schema: type | None = None,
        model_purpose: str | None = None,
        thinking_budget: int | None = None,
        request_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Generate meal plan — runs on the caller's event loop.

        ``request_timeout_seconds`` overrides the provider's per-request
        timeout for a call that writes far more than a single answer.

        All callers must be async. Bridging sync→async via a blocking loop driver
        inside an already-running loop causes nesting bugs with async-bound
        resources (Redis, HTTP clients).
        """
