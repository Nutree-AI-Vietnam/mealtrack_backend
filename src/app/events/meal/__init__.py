"""
Meal domain events.
"""

from src.app.events.meal.meal_analysis_started_event import MealAnalysisStartedEvent
from src.app.events.meal.meal_edited_event import MealEditedEvent
from src.app.events.meal.meal_events import (
    MealCreatedEvent,
    MealDeletedEvent,
    MealUpdatedEvent,
    publish_meal_event,
)
from src.app.events.meal.meal_insight_snapshot import MealInsightSnapshot
from src.app.events.meal.meal_nutrition_updated_event import MealNutritionUpdatedEvent

__all__ = [
    "MealAnalysisStartedEvent",
    "MealNutritionUpdatedEvent",
    "MealEditedEvent",
    "MealCreatedEvent",
    "MealUpdatedEvent",
    "MealDeletedEvent",
    "MealInsightSnapshot",
    "publish_meal_event",
]
