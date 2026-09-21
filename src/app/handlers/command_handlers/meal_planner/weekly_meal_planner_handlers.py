"""CQRS handlers for weekly meal planner state transitions."""

from typing import Any

from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    LogMealPlanSlotCommand,
    UpdateMealPlanPantryStockCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.weekly_meal_logging_service import WeeklyMealLoggingService
from src.app.services.weekly_meal_plan_service import WeeklyMealPlanService
from src.app.services.weekly_pantry_service import WeeklyPantryService


@handles(GenerateWeeklyMealPlanCommand)
class GenerateWeeklyMealPlanCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyMealPlanService(uow_factory)

    async def handle(self, command):
        return await self.service.generate(command)


@handles(UpdateWeeklyMealPlanCommand)
class UpdateWeeklyMealPlanCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyMealPlanService(uow_factory)

    async def handle(self, command):
        return await self.service.update(command)


@handles(AiAdjustMealPlanCommand)
class AiAdjustMealPlanCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any, ai_adjustment_provider=None):
        self.service = WeeklyMealPlanService(
            uow_factory, ai_adjustment_provider=ai_adjustment_provider
        )

    async def handle(self, command):
        return await self.service.ai_proposal(command)


@handles(UpdateMealPlanPantryStockCommand)
class UpdateMealPlanPantryStockCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyPantryService(uow_factory)

    async def handle(self, command):
        return await self.service.update(command)


@handles(LogMealPlanSlotCommand)
class LogMealPlanSlotCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyMealLoggingService(uow_factory)

    async def handle(self, command):
        return await self.service.log(command)
