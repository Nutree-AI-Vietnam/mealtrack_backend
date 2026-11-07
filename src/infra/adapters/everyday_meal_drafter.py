"""Find one everyday meal with the existing meal-generation provider."""

from __future__ import annotations

import asyncio
import logging

from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.everyday_catalog import EverydaySlot, build_everyday_prompt

logger = logging.getLogger(__name__)

_ATTEMPT_TIMEOUT_SECONDS = 45


class EverydayMealDrafter:
    """One provider call per dish. The catalog job decides whether to keep it."""

    def __init__(self, generation: MealGenerationServicePort):
        self._generation = generation

    async def find(
        self, slot: EverydaySlot, *, exclude_names: tuple[str, ...]
    ) -> object | None:
        system, prompt = build_everyday_prompt(slot, exclude_names=exclude_names)
        try:
            return await asyncio.wait_for(
                self._generation.generate_meal_plan_async(
                    prompt,
                    system,
                    "json",
                    1200,
                    None,
                    "recipe",
                ),
                timeout=_ATTEMPT_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "everyday catalog draft failed market=%s family=%s",
                slot.market,
                slot.dish_family,
                exc_info=True,
            )
            return None
