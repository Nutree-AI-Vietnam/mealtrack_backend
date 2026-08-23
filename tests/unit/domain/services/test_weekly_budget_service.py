"""
Unit tests for WeeklyBudgetService.

Sync I/O methods (`calculate_weekly_consumed`, `get_effective_adjusted_daily`)
were deleted as dead code (zero production callers — superseded by the
`_async` twins). Their scenarios were ported to
`test_weekly_budget_async.py` before deletion. This file keeps pure-math
coverage only (`calculate_adjusted_daily`, deficit cap, cheat-day suggestion,
`WeeklyMacroBudget` domain).
"""

from datetime import date

import pytest

from src.domain.constants import WeeklyBudgetConstants
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    WeeklyBudgetService,
)


class TestWeeklyBudgetService:
    """Test weekly budget calculations."""

    def test_calculate_adjusted_daily_normal(self):
        """Test adjusted daily calculation when under budget.

        Calories derived from macros: P×4 + C×4 + F×9.
        Daily: 70×4 + 200×4 + 70×9 = 280+800+630 = 1710 kcal/day.
        Weekly target: 1710×7 = 11970, consumed 3 days: 5130.
        Remaining: 6840 / 4 = 1710/day.
        """
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-1",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=11970,  # 1710 * 7 (macro-consistent)
            target_protein=490,  # 70 * 7
            target_carbs=1400,  # 200 * 7
            target_fat=490,  # 70 * 7
            consumed_calories=5130,  # 3 days at 1710
            consumed_protein=210,  # 3 * 70
            consumed_carbs=600,  # 3 * 200
            consumed_fat=210,  # 3 * 70
        )

        # 4 days remaining — macros: P=70, C=200, F=70 → 1710 kcal/day
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1710,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=70,
            bmr=1400,
            remaining_days=4,
        )

        # Derived: 70×4 + 200×4 + 70×9 = 1710
        assert result.calories == 1710
        assert result.bmr_floor_active is False
        assert result.remaining_days == 4

    def test_calculate_adjusted_daily_with_bmr_floor(self):
        """Test BMR floor activates when deficit cap still leaves calories below BMR.

        Uses a very high BMR (1900) that exceeds deficit cap (2000*0.9=1800).
        """
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-2",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=14000,
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=12000,  # Only 2000 left for 6 days
            consumed_protein=420,
            consumed_carbs=1200,
            consumed_fat=420,
        )

        # BMR=1900 > deficit cap floor (2000*0.9=1800), so BMR floor wins
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2000,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=70,
            bmr=1900,
            remaining_days=6,
        )

        assert result.calories == pytest.approx(1900, abs=1)
        assert result.bmr_floor_active is True

    def test_deficit_cap_no_cap_needed(self):
        """Deficit within 10% — adjusted stays as-is, no cap distortion.

        Daily: P=70, C=200, F=70 → 1710/day. Weekly=11970.
        Consumed 4 days exactly at target: 6840. Remaining: 5130 / 3 = 1710.
        1710 == base → 0% deviation, no cap needed.
        """
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-1",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=11970,  # 1710 * 7
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=6840,
            consumed_protein=280,
            consumed_carbs=800,
            consumed_fat=280,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1710,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=70,
            bmr=1400,
            remaining_days=3,
        )
        # Should equal base exactly (no cap needed)
        assert result.calories == 1710
        assert result.bmr_floor_active is False

    def test_deficit_cap_triggered_2000_base(self):
        """Deficit exceeds 10% for 2000-cal user — capped to 1800."""
        # Build budget so adjusted would be ~1700 without cap
        # Daily: P=70, C=200, F=70 → 1710/day, weekly=11970
        # Need remaining macros to produce ~1700/day with 2 days left
        # remaining_carbs/2 ~ 150, remaining_fat/2 ~ 50 → C=150, F=50
        # Calories from macros: 70*4 + 150*4 + 50*9 = 280+600+450 = 1330
        # That's way below cap, so cap will kick in
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-2",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=11970,
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=9600,
            consumed_protein=350,
            consumed_carbs=1100,
            consumed_fat=390,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1710,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=70,
            bmr=1200,
            remaining_days=2,
        )
        # Cap: 1710 * 0.90 = 1539
        min_allowed = 1710 * 0.90
        assert result.calories >= min_allowed - 1  # tolerance for rounding
        assert result.protein == 70  # protein unchanged

    def test_deficit_cap_triggered_1500_base(self):
        """Deficit exceeds 10% for 1500-cal user — fairer than fixed -200."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-3",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=10500,  # 1500 * 7
            target_protein=420,  # 60 * 7
            target_carbs=1312.5,  # 187.5 * 7
            target_fat=350,  # 50 * 7
            consumed_calories=9000,
            consumed_protein=360,
            consumed_carbs=1200,
            consumed_fat=310,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1500,
            standard_daily_carbs=187.5,
            standard_daily_fat=50,
            standard_daily_protein=60,
            bmr=1100,
            remaining_days=2,
        )
        # Cap: 1500 * 0.90 = 1350, much less than a fixed -200 (1300)
        min_allowed = 1500 * 0.90
        assert result.calories >= min_allowed - 1
        assert result.protein == 60

    def test_deficit_cap_protein_unchanged(self):
        """Protein stays fixed regardless of deficit cap activation."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-prot",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=14000,
            target_protein=980,  # 140 * 7
            target_carbs=1400,
            target_fat=490,
            consumed_calories=13000,
            consumed_protein=840,
            consumed_carbs=1300,
            consumed_fat=460,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2000,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=140,
            bmr=1400,
            remaining_days=1,
        )
        assert result.protein == 140  # never touched

    def test_under_eating_surplus_is_capped(self):
        """Under-eating raises targets, but not into extreme catch-up eating."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-surplus",
            user_id="user-1",
            week_start_date=date(2026, 2, 16),
            target_calories=14000,
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=3000,  # very low consumption
            consumed_protein=100,
            consumed_carbs=300,
            consumed_fat=100,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2000,
            standard_daily_carbs=200,
            standard_daily_fat=70,
            standard_daily_protein=70,
            bmr=1400,
            remaining_days=4,
        )
        # Raw redistribution would be 2750; cap and macro ceilings keep it safer.
        assert result.calories > 2000
        assert result.calories <= 2000 * (
            1 + WeeklyBudgetConstants.MAX_DAILY_SURPLUS_RATIO
        )
        assert result.carbs <= 200 * WeeklyBudgetConstants.MACRO_CEILING_RATIO
        assert result.fat <= 70 * WeeklyBudgetConstants.MACRO_CEILING_RATIO
        assert result.bmr_floor_active is False

    def test_calorie_cap_fat_heavy_surplus(self):
        """Calorie cap: over-eating fat + under-eating carbs should NOT inflate target.

        Real user scenario: base=2456, consumed Mon-Wed heavy on fat/protein,
        light on carbs. Without cap, macro redistribution gives 2570 (+114).
        With cap, should be ~2417 (-39).
        """
        # Base daily: P=114, C=346.6, F=68.2 → 2456 cal
        # Weekly: P=798, C=2426.2, F=477.4 → 17192 cal
        # Consumed Mon-Wed: P=494, C=784, F=268 → ~7521 cal
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-fat",
            user_id="user-1",
            week_start_date=date(2026, 3, 23),
            target_calories=17192,
            target_protein=798,
            target_carbs=2426.2,
            target_fat=477.4,
            consumed_calories=7521,
            consumed_protein=494,
            consumed_carbs=784,
            consumed_fat=268,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2456,
            standard_daily_carbs=346.6,
            standard_daily_fat=68.2,
            standard_daily_protein=114,
            bmr=1400,
            remaining_days=4,
        )
        # Must be BELOW base (user is in calorie surplus)
        assert result.calories < 2456
        # Calorie-redistributed = (17192-7521)/4 = 2417.75
        assert result.calories == pytest.approx(2417, abs=5)
        assert result.protein == 114
        # Carbs should still be above base (preserves "eat more carbs" signal)
        assert result.carbs > 346.6
        # Fat should be below base (user over-ate fat)
        assert result.fat < 68.2

    def test_moderate_under_eating_still_raises_calories(self):
        """Moderate under-eating raises target without exceeding surplus cap."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-under",
            user_id="user-1",
            week_start_date=date(2026, 3, 23),
            target_calories=17192,
            target_protein=798,
            target_carbs=2426.2,
            target_fat=477.4,
            consumed_calories=5000,  # Well under target for 3 days
            consumed_protein=300,
            consumed_carbs=600,
            consumed_fat=150,
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2456,
            standard_daily_carbs=346.6,
            standard_daily_fat=68.2,
            standard_daily_protein=114,
            bmr=1400,
            remaining_days=4,
        )
        # Under-eating: adjusted should be ABOVE base
        assert result.calories > 2456
        assert result.calories <= 2456 * (
            1 + WeeklyBudgetConstants.MAX_DAILY_SURPLUS_RATIO
        )

    def test_under_eating_raises_calories_even_when_fat_is_over(self):
        """Calorie balance leads adjustment; one macro overage must not lower calories."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-under-fat-over",
            user_id="user-1",
            week_start_date=date(2026, 6, 1),
            target_calories=12495,  # 1785 * 7
            target_protein=1099,  # 157 * 7
            target_carbs=1127,  # 161 * 7
            target_fat=399,  # 57 * 7
            consumed_calories=1141,
            consumed_protein=78,
            consumed_carbs=58,
            consumed_fat=66,  # fat over daily target, calories still under
        )

        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1785,
            standard_daily_carbs=161,
            standard_daily_fat=57,
            standard_daily_protein=157,
            bmr=1400,
            remaining_days=6,
        )

        assert result.calories > 1785
        assert result.calories == pytest.approx((12495 - 1141) / 6, abs=2)
        assert result.protein == 157

    def test_extreme_under_eating_respects_macro_ceilings_after_fit(self):
        """Calorie fitting must not push adaptive carbs/fat past safe ceilings."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-extreme-under",
            user_id="user-1",
            week_start_date=date(2026, 6, 1),
            target_calories=12495,
            target_protein=1099,
            target_carbs=1127,
            target_fat=399,
            consumed_calories=0,
            consumed_protein=0,
            consumed_carbs=0,
            consumed_fat=0,
        )

        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=1785,
            standard_daily_carbs=161,
            standard_daily_fat=57,
            standard_daily_protein=157,
            bmr=1400,
            remaining_days=2,
        )

        assert result.calories <= 1785 * (
            1 + WeeklyBudgetConstants.MAX_DAILY_SURPLUS_RATIO
        )
        assert result.carbs <= 161 * WeeklyBudgetConstants.MACRO_CEILING_RATIO
        assert result.fat <= 57 * WeeklyBudgetConstants.MACRO_CEILING_RATIO

    def test_calorie_cap_macros_balanced_no_cap(self):
        """When macros are balanced, macro-derived ≈ calorie-redistributed. No cap."""
        # Consume exactly at base ratios for 3 days
        budget = WeeklyMacroBudget(
            weekly_budget_id="test-cap-balanced",
            user_id="user-1",
            week_start_date=date(2026, 3, 23),
            target_calories=17192,
            target_protein=798,
            target_carbs=2426.2,
            target_fat=477.4,
            consumed_calories=7368,  # 3 × 2456
            consumed_protein=342,  # 3 × 114
            consumed_carbs=1039.8,  # 3 × 346.6
            consumed_fat=204.6,  # 3 × 68.2
        )
        result = WeeklyBudgetService.calculate_adjusted_daily(
            weekly_budget=budget,
            standard_daily_calories=2456,
            standard_daily_carbs=346.6,
            standard_daily_fat=68.2,
            standard_daily_protein=114,
            bmr=1400,
            remaining_days=4,
        )
        # Balanced consumption: should be close to base
        assert result.calories == pytest.approx(2456, abs=5)

    def test_should_suggest_cheat_day_above_threshold(self):
        """Test cheat day suggestion triggers above threshold."""
        result = WeeklyBudgetService.should_suggest_cheat_day(
            daily_consumed=2500,  # 125% of 2000
            daily_target=2000,
            is_already_cheat_day=False,
        )
        assert result is True

    def test_should_not_suggest_cheat_day_below_threshold(self):
        """Test cheat day suggestion doesn't trigger below threshold."""
        result = WeeklyBudgetService.should_suggest_cheat_day(
            daily_consumed=1800,  # 90% of 2000
            daily_target=2000,
            is_already_cheat_day=False,
        )
        assert result is False

    def test_should_not_suggest_for_already_cheat_day(self):
        """Test no suggestion when already a cheat day."""
        result = WeeklyBudgetService.should_suggest_cheat_day(
            daily_consumed=2500,
            daily_target=2000,
            is_already_cheat_day=True,
        )
        assert result is False

    def test_calculate_remaining_days(self):
        """Test remaining days calculation."""
        # Monday to Sunday = 7 days
        monday = date(2026, 2, 16)
        assert WeeklyBudgetService.calculate_remaining_days(monday, monday) == 7

        # Wednesday = 5 days remaining
        wednesday = date(2026, 2, 18)
        assert WeeklyBudgetService.calculate_remaining_days(monday, wednesday) == 5

        # Next week = 0 days
        next_monday = date(2026, 2, 23)
        assert WeeklyBudgetService.calculate_remaining_days(monday, next_monday) == 0


class TestWeeklyMacroBudgetDomain:
    """Test WeeklyMacroBudget domain entity."""

    def test_remaining_calories(self):
        """Test remaining calories calculation."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test",
            user_id="user",
            week_start_date=date.today(),
            target_calories=14000,
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=6000,
        )

        assert budget.remaining_calories == 8000

    def test_consumption_percentage(self):
        """Test consumption percentage calculation."""
        budget = WeeklyMacroBudget(
            weekly_budget_id="test",
            user_id="user",
            week_start_date=date.today(),
            target_calories=10000,
            target_protein=490,
            target_carbs=1400,
            target_fat=490,
            consumed_calories=5000,
        )

        assert budget.consumption_percentage == 50.0


class TestLeftoverBudgetCap:
    """Leftover weekly split must not undo the deficit/BMR floor."""

    def test_calorie_safety_floor_uses_deficit_cap_when_higher_than_bmr(self):
        floor = WeeklyBudgetService.calorie_safety_floor(2000, bmr=1500)
        assert floor == 1800

    def test_calorie_safety_floor_uses_bmr_when_higher_than_deficit_cap(self):
        floor = WeeklyBudgetService.calorie_safety_floor(2000, bmr=1900)
        assert floor == 1900

    def test_large_overeating_does_not_drop_below_floor(self):
        """8,000 Monday leaves 6,000 / 6 days = 1,000 leftover; floor holds 1,800."""
        adjusted = AdjustedDailyTargets(
            calories=1800,
            carbs=171.4,
            fat=57.1,
            protein=150,
            bmr_floor_active=False,
            remaining_days=6,
        )
        result = WeeklyBudgetService.apply_leftover_budget_cap(
            adjusted,
            remaining_before_today=6000,
            remaining_days=6,
            standard_daily_calories=2000,
            bmr=1600,
        )
        assert result.calories == 1800
        assert result.protein == 150

    def test_leftover_split_still_lowers_a_day_above_the_floor(self):
        """11,400 leftover / 6 days = 1,900, which is still above the 1,800 floor."""
        adjusted = AdjustedDailyTargets(
            calories=2000,
            carbs=200,
            fat=67,
            protein=150,
            bmr_floor_active=False,
            remaining_days=6,
        )
        result = WeeklyBudgetService.apply_leftover_budget_cap(
            adjusted,
            remaining_before_today=11400,
            remaining_days=6,
            standard_daily_calories=2000,
            bmr=1600,
        )
        assert result.calories == pytest.approx(1900, abs=1)
        assert result.protein == 150
        assert result.carbs < 200

    def test_empty_leftover_skips_cap(self):
        adjusted = AdjustedDailyTargets(
            calories=1800,
            carbs=171.4,
            fat=57.1,
            protein=150,
            bmr_floor_active=False,
            remaining_days=3,
        )
        result = WeeklyBudgetService.apply_leftover_budget_cap(
            adjusted,
            remaining_before_today=0,
            remaining_days=3,
            standard_daily_calories=2000,
            bmr=1600,
        )
        assert result.calories == 1800
