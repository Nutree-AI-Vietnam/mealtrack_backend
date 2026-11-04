"""What a hundred grams of food can physically be."""

from src.domain.model.nutrition.plausibility import (
    is_plausible_per_100g,
    per_100g_problem,
)


class TestRealFoodPasses:
    def test_chicken_thigh(self):
        assert is_plausible_per_100g(24.9, 0.0, 15.4)

    def test_olive_oil_is_the_densest_thing_there_is(self):
        assert is_plausible_per_100g(0.0, 0.0, 100.0)

    def test_pure_sugar(self):
        assert is_plausible_per_100g(0.0, 100.0, 0.0)

    def test_salt_and_water_read_zero_and_that_is_correct(self):
        """Zero is left alone: telling salt from a bad lookup needs a word list."""
        assert is_plausible_per_100g(0.0, 0.0, 0.0)

    def test_high_fibre_food(self):
        assert is_plausible_per_100g(13.0, 61.0, 3.0, fiber=10.0)


class TestImpossibleReadingsAreNamed:
    def test_the_chicken_thigh_row_that_is_live_in_staging(self):
        """475g protein and 475g fat in 100g of food — 6175 kcal."""
        assert per_100g_problem(475.0, 0.0, 475.0) == "mass"

    def test_a_hundred_grams_cannot_hold_more_than_a_hundred(self):
        assert per_100g_problem(40.0, 40.0, 40.0) == "mass"

    def test_denser_than_fat_is_not_food(self):
        """Inside the mass bound, over the energy bound: 904 kcal in 101 g."""
        assert per_100g_problem(0.0, 0.0, 99.9) is None  # 899 kcal
        assert per_100g_problem(1.0, 0.0, 100.0) == "energy"  # 904 kcal

    def test_negative_macros(self):
        assert per_100g_problem(-1.0, 10.0, 2.0) == "negative"

    def test_missing_macros(self):
        assert per_100g_problem(None, 10.0, 2.0) == "missing"

    def test_rounding_slack_does_not_trip_the_mass_bound(self):
        assert per_100g_problem(0.1, 100.0, 0.1) is None
