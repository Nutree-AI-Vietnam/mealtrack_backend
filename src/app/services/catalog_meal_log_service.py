"""Materialize a logged catalog meal into a diary meal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.app.commands.meal_catalog import LogCatalogMealCommand
from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.domain.model.meal import Meal
from src.domain.model.meal_recommendation import CatalogMeal


@dataclass(frozen=True)
class LogCatalogMealResult:
    meal_id: str
    catalog_meal_id: str
    logged_via: str
    # Kept on the contract from when a catalog log could close a plan slot.
    # Always None now that the week is the only plan.
    plan_id: str | None
    slot_id: str | None
    meal_date: date
    meal_type: str
    meal: Meal

    @property
    def logged_meal_id(self) -> str:
        return self.meal_id

    def to_replay_payload(self) -> dict[str, str | None]:
        return {
            "meal_id": self.meal_id,
            "catalog_meal_id": self.catalog_meal_id,
            "logged_via": self.logged_via,
            "plan_id": self.plan_id,
            "slot_id": self.slot_id,
            "logged_meal_id": self.meal_id,
            "meal_date": self.meal_date.isoformat(),
            "meal_type": self.meal_type,
        }


class CatalogMealLogService:
    """Write a catalog meal into the diary."""

    def __init__(
        self,
        materializer: RecommendedMealMaterializationService | None = None,
    ) -> None:
        self._materializer = materializer or RecommendedMealMaterializationService()

    async def execute(
        self,
        uow,
        command: LogCatalogMealCommand,
        catalog_meal: CatalogMeal,
    ) -> LogCatalogMealResult:
        meal = await self._materializer.materialize_from_catalog(
            uow,
            user_id=command.user_id,
            catalog_meal=catalog_meal,
            meal_date=command.meal_date,
            meal_type=command.meal_type,
            timezone=command.timezone,
        )
        return LogCatalogMealResult(
            meal_id=meal.meal_id,
            catalog_meal_id=catalog_meal.id,
            logged_via="catalog",
            plan_id=None,
            slot_id=None,
            meal_date=command.meal_date,
            meal_type=command.meal_type,
            meal=meal,
        )
