"""Catalog numbers every hydration write path shares (tap, caloric tap, describe)."""

from datetime import datetime
from uuid import uuid4

from src.domain.model.hydration import Drink
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition


def catalog_macros(drink: Drink, volume_ml: int) -> Macros:
    """Macros for a volume of a catalog drink, from its kcal and sugar per 100 ml."""
    fat_100ml = max(0.0, (drink.kcal_per_100ml - drink.sugar_per_100ml * 4) / 9)
    carbs_100ml = max(0.0, (drink.kcal_per_100ml - fat_100ml * 9) / 4)
    volume_factor = volume_ml / 100.0
    return Macros(
        protein=0.0,
        carbs=round(carbs_100ml * volume_factor, 1),
        fat=round(fat_100ml * volume_factor, 1),
        fiber=0.0,
        sugar=round(drink.sugar_per_100ml * volume_factor, 1),
    )


def water_macros(drink: Drink, volume_ml: int) -> Macros:
    """Macros of the legacy meal; zero-kcal drinks carry none."""
    if drink.kcal_per_100ml > 0:
        return catalog_macros(drink, volume_ml)
    return Macros(protein=0.0, carbs=0.0, fat=0.0, fiber=0.0, sugar=0.0)


def water_meal_for(
    drink: Drink,
    volume_ml: int,
    logged_at: datetime,
    user_id: str,
    *,
    logged_on_day: bool,
) -> Meal:
    """The legacy hydration Meal a water-category drink saves beside its entry."""
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        created_at=logged_at,
        ready_at=logged_at,
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1, url=None),
        dish_name=drink.name,
        meal_type="hydration",
        source="hydration",
        quantity=drink.credited_ml_for_volume(volume_ml),
        nutrition=Nutrition(macros=water_macros(drink, volume_ml), food_items=None),
        logged_on_day=logged_on_day,
    )
