"""CQRS handlers for weekly meal planner state transitions."""

from typing import Any

from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    LinkMealPlanSlotCommand,
    LogMealPlanSlotCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
    UpdateGroceryDayLinesCommand,
    UpdateMealPlanPantryStockCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.weekly_grocery_day_line_service import (
    WeeklyGroceryDayLineService,
)
from src.app.services.weekly_meal_logging_service import WeeklyMealLoggingService
from src.app.services.weekly_meal_plan_service import WeeklyMealPlanService
from src.app.services.weekly_meal_slot_state_service import (
    WeeklyMealSlotStateService,
)
from src.app.services.weekly_pantry_service import WeeklyPantryService


@handles(GenerateWeeklyMealPlanCommand)
class GenerateWeeklyMealPlanCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any, *, compact_week_percent: int = 0):
        self.service = WeeklyMealPlanService(
            uow_factory, compact_week_percent=compact_week_percent
        )

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


@handles(UpdateGroceryDayLinesCommand)
class UpdateGroceryDayLinesCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyGroceryDayLineService(uow_factory)

    async def handle(self, command):
        return await self.service.update(command)


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


@handles(UnlogMealPlanSlotCommand)
class UnlogMealPlanSlotCommandHandler(EventHandler):
    def __init__(
        self,
        uow_factory: Any,
        *,
        event_publisher: Any = None,
        environment: str = "development",
    ):
        self.service = WeeklyMealSlotStateService(
            uow_factory, event_publisher=event_publisher, environment=environment
        )

    async def handle(self, command):
        return await self.service.unlog(command)


@handles(LinkMealPlanSlotCommand)
class LinkMealPlanSlotCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyMealSlotStateService(uow_factory)

    async def handle(self, command):
        return await self.service.link(command)


@handles(SetMealPlanSlotSkipCommand)
class SetMealPlanSlotSkipCommandHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyMealSlotStateService(uow_factory)

    async def handle(self, command):
        return await self.service.set_skip(command)
