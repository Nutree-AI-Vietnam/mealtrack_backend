"""CQRS query handlers for weekly planner read projections."""

from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.meal_planner import (
    GetCurrentWeeklyPlanQuery,
    GetRecipeDetailQuery,
    GetWeeklyGroceriesQuery,
    ListRecipesQuery,
)
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.app.services.weekly_recipe_service import WeeklyRecipeService


@handles(GetCurrentWeeklyPlanQuery)
class GetCurrentWeeklyPlanQueryHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.uow_factory = uow_factory

    async def handle(self, query):
        async with self.uow_factory() as uow:
            return await uow.weekly_meal_plans.get_current(
                user_id=query.user_id, week_start_date=query.week_start_date
            )


@handles(ListRecipesQuery)
class ListRecipesQueryHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyRecipeService(uow_factory)

    async def handle(self, query):
        return await self.service.list(
            query=query.query,
            diet=query.diet,
            max_cook_time=query.max_cook_time,
            cuisine=query.cuisine,
            allergies=query.allergies,
            dislikes=query.dislikes,
            limit=query.limit,
            offset=query.offset,
        )


@handles(GetRecipeDetailQuery)
class GetRecipeDetailQueryHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.service = WeeklyRecipeService(uow_factory)

    async def handle(self, query):
        return await self.service.detail(query.recipe_id)


@handles(GetWeeklyGroceriesQuery)
class GetWeeklyGroceriesQueryHandler(EventHandler):
    def __init__(self, uow_factory: Any):
        self.uow_factory = uow_factory
        self.grocery_service = WeeklyGroceryService()

    async def handle(self, query):
        async with self.uow_factory() as uow:
            plan = await uow.weekly_meal_plans.get_by_id(
                user_id=query.user_id, plan_id=query.plan_id
            )
            if plan is None:
                return None
            return plan, await self.grocery_service.calculate(uow, plan)
