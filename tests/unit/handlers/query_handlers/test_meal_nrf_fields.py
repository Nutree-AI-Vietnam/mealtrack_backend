"""Per-item NRF helper — must not reuse day-blend zeroing."""

from src.domain.services.meal_nrf_fields import (
    hydration_entry_nrf_fields,
    meal_nrf_fields,
)
from src.domain.model import FoodItem, Macros, Nutrition
from src.domain.model.nutrition.micros import Micros
from src.domain.services.nrf_score import nrf_progress_fields, nrf_quality


class _Meal:
    def __init__(self, nutrition):
        self.nutrition = nutrition


class _Entry:
    def __init__(self, *, protein=0.0, fiber=0.0, micros=None):
        self.protein_g = protein
        self.fiber_g = fiber
        self.micros = micros


def test_meal_nrf_shows_quality_below_day_coverage_gate():
    micros = Micros(iron=7.6)
    nutrition = Nutrition(
        macros=Macros(protein=34, carbs=62, fat=22, fiber=8.8),
        food_items=[
            FoodItem(
                id="i",
                name="Broth",
                quantity=100,
                unit="g",
                macros=Macros(protein=34, carbs=62, fat=22, fiber=8.8),
                micros=micros,
            )
        ],
    )
    fields = meal_nrf_fields(_Meal(nutrition))
    assert fields["nrf_coverage"] == 1
    assert fields["nrf_quality"] == nrf_quality(34, 8.8, micros)
    assert nrf_progress_fields(34, 8.8, micros)["nrf_quality"] == 0.0


def test_water_entry_has_no_quality():
    fields = hydration_entry_nrf_fields(_Entry())
    assert fields == {"nrf_quality": None, "nrf_coverage": 0}
