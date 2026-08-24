"""
Meal domain events.
"""

from src.app.events.meal.meal_analysis_started_event import MealAnalysisStartedEvent
from src.app.events.meal.meal_edited_event import MealEditedEvent
from src.app.events.meal.meal_events import (
    MealCreatedEvent,
    MealDeletedEvent,
    MealUpdatedEvent,
)
from src.app.events.meal.meal_nutrition_updated_event import MealNutritionUpdatedEvent

__all__ = [
    "MealAnalysisStartedEvent",
    "MealNutritionUpdatedEvent",
    "MealEditedEvent",
    "MealCreatedEvent",
    "MealUpdatedEvent",
    "MealDeletedEvent",
]
