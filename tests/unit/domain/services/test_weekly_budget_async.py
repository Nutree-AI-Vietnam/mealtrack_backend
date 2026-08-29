"""
Unit tests for async methods in WeeklyBudgetService.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.model.meal import MealStatus
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import WeeklyBudgetService


def _ready_meal(*, protein: float, carbs: float, fat: float, created_at: datetime):
    from src.domain.model.meal import MealStatus

    meal = Mock()
    meal.status = MealStatus.READY
    meal.created_at = created_at
    meal.source = "scan"
    meal.food_label_metadata = None
    meal.nutrition.nutrition_override = None
    meal.nutrition.food_items = []
    meal.nutrition.macros.protein = protein
    meal.nutrition.macros.carbs = carbs
    meal.nutrition.macros.fat = fat
    meal.nutrition.macros.fiber = 0.0
    meal.nutrition.calories = protein * 4 + carbs * 4 + fat * 9
    return meal


class _FakeMovementEntries:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    async def sum_included_kcal_for_range(self, user_id, start_utc, end_utc):
        self.calls.append((user_id, start_utc, end_utc))
        return sum(
            kcal
            for logged_at, kcal, include in self.entries
            if include and start_utc <= logged_at < end_utc
        )


# --- Fakes ported from test_weekly_budget_service.py (sync I/O scenarios) ---
# Plain async-repo fakes (no movement_entries attr) so
# `_calculate_movement_kcal_async` short-circuits to 0.0, matching the deleted
# sync path's behavior exactly for parity purposes.


@dataclass
class FakeNutritionMacros:
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    fiber: float = 0.0


@dataclass
class FakeNutrition:
    calories: float = 0.0
    macros: FakeNutritionMacros = None
    nutrition_override: object = None
    food_items: list = None

    def __post_init__(self):
        if self.macros is None:
            self.macros = FakeNutritionMacros()
        if self.food_items is None:
            self.food_items = []


@dataclass
class FakeMeal:
    status: MealStatus = MealStatus.READY
    nutrition: FakeNutrition | None = None
    created_at: datetime | None = None
    source: str = "scan"
    food_label_metadata: object = None


@dataclass
class FakeCheatDay:
    date: date = None


class FakeMealRepoAsync:
    """Async-repo fake mirroring FakeMealRepo from test_weekly_budget_service.py."""

    def __init__(self, meals: list[FakeMeal] = None, daily_counts: dict = None):
        self._meals = meals or []
        self._daily_counts = daily_counts or {}

    async def find_by_date_range(
        self, user_id, start, end, user_timezone=None, **kwargs
    ):
        return self._meals

    async def get_daily_meal_counts(self, user_id, start, end, user_timezone=None):
        return self._daily_counts


class FakeCheatDayRepoAsync:
    def __init__(self, cheat_days: list[FakeCheatDay] = None):
        self._cheat_days = cheat_days or []

    async def find_by_user_and_date_range(self, user_id, start, end):
        return self._cheat_days


class FakeUoWAsync:
    """Async Unit of Work fake — no movement_entries attr by design (see above)."""

    def __init__(self, meals=None, daily_counts=None, cheat_days=None):
        self.meals = FakeMealRepoAsync(meals or [], daily_counts or {})
        self.cheat_days = FakeCheatDayRepoAsync(cheat_days or [])


def _make_budget(
    week_start=date(2026, 3, 23),
    consumed_cal=0.0,
    consumed_p=0.0,
    consumed_c=0.0,
    consumed_f=0.0,
):
    """Weekly budget: 2000 cal/day → 14000/wk, P=70, C=250, F=70."""
    return WeeklyMacroBudget(
        weekly_budget_id="eff-test",
        user_id="user-1",
        week_start_date=week_start,
        target_calories=14000,
        target_protein=490,  # 70 * 7
        target_carbs=1750,  # 250 * 7
        target_fat=490,  # 70 * 7
        consumed_calories=consumed_cal,
        consumed_protein=consumed_p,
        consumed_carbs=consumed_c,
        consumed_fat=consumed_f,
    )


# Base daily values matching _make_budget
_BASE_CAL = 2000.0
_BASE_P = 70.0
_BASE_C = 250.0
_BASE_F = 70.0
_BMR = 1500.0


class TestCalculateWeeklyConsumedAsync:
    """Test calculate_weekly_consumed_async method."""

    @pytest.mark.asyncio
    async def test_returns_zero_totals_when_no_meals(self):
        """Should return zero totals when no meals found."""
        mock_uow = Mock()
        mock_uow.meals.find_by_date_range = AsyncMock(return_value=[])

        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow=mock_uow,
            user_id="user123",
            week_start=date(2026, 3, 9),
        )

        assert result == {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}

    @pytest.mark.asyncio
    async def test_sums_ready_meals_only(self):
        """Should only sum meals with READY status."""
        from src.domain.model.meal import MealStatus

        ready_meal = Mock()
        ready_meal.status = MealStatus.READY
        ready_meal.source = "scan"
        ready_meal.food_label_metadata = None
        ready_meal.nutrition.nutrition_override = None
        ready_meal.nutrition.food_items = []
        ready_meal.nutrition.calories = 495
        ready_meal.nutrition.macros.protein = 30
        ready_meal.nutrition.macros.carbs = 60
        ready_meal.nutrition.macros.fat = 15
        ready_meal.created_at = None

        processing_meal = Mock()
        processing_meal.status = MealStatus.PROCESSING
        processing_meal.source = "scan"
        processing_meal.food_label_metadata = None
        processing_meal.nutrition.nutrition_override = None
        processing_meal.nutrition.food_items = []
        processing_meal.nutrition.calories = 300
        processing_meal.nutrition.macros.protein = 20
        processing_meal.nutrition.macros.carbs = 40
        processing_meal.nutrition.macros.fat = 10
        processing_meal.created_at = None

        mock_uow = Mock()
        mock_uow.meals.find_by_date_range = AsyncMock(
            return_value=[ready_meal, processing_meal]
        )

        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow=mock_uow,
            user_id="user123",
            week_start=date(2026, 3, 9),
        )

        assert result["calories"] == 495
        assert result["protein"] == 30
        assert result["carbs"] == 60
        assert result["fat"] == 15

    @pytest.mark.asyncio
    async def test_subtracts_included_movement_from_calories_only(self):
        """Movement credit affects calorie balance, not food macro grams."""
        meal = _ready_meal(
            protein=100,
            carbs=250,
            fat=100,
            created_at=datetime(2026, 3, 9, 12, tzinfo=UTC),
        )
        mock_uow = Mock()
        mock_uow.meals.find_by_date_range = AsyncMock(return_value=[meal])
        mock_uow.movement_entries = _FakeMovementEntries(
            [(datetime(2026, 3, 9, 18, tzinfo=UTC), 200.0, True)]
        )

        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow=mock_uow,
            user_id="user123",
            week_start=date(2026, 3, 9),
            user_timezone="UTC",
        )

        assert result["calories"] == 2100.0
        assert result["protein"] == 100
        assert result["carbs"] == 250
        assert result["fat"] == 100

    @pytest.mark.asyncio
    async def test_ignores_excluded_and_future_movement(self):
        """Only included movement inside the requested local date range counts."""
        meal = _ready_meal(
            protein=100,
            carbs=250,
            fat=100,
            created_at=datetime(2026, 3, 9, 12, tzinfo=UTC),
        )
        mock_uow = Mock()
        mock_uow.meals.find_by_date_range = AsyncMock(return_value=[meal])
        mock_uow.movement_entries = _FakeMovementEntries(
            [
                (datetime(2026, 3, 9, 18, tzinfo=UTC), 200.0, False),
                (datetime(2026, 3, 10, 18, tzinfo=UTC), 500.0, True),
            ]
        )

        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow=mock_uow,
            user_id="user123",
            week_start=date(2026, 3, 9),
            end_date=date(2026, 3, 9),
            user_timezone="UTC",
        )

        assert result["calories"] == 2300.0

    # --- Ported from TestCalculateWeeklyConsumed (sync) — see scout matrix ---

    @pytest.mark.asyncio
    async def test_no_meals_returns_zeros(self):
        uow = FakeUoWAsync(meals=[])
        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
        )
        assert result == {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    @pytest.mark.asyncio
    async def test_sums_ready_meals(self):
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=9999,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=9999,
                    macros=FakeNutritionMacros(protein=40, carbs=80, fat=25),
                ),
                created_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
            ),
        ]
        uow = FakeUoWAsync(meals=meals)
        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
        )
        assert result["calories"] == 1205
        assert result["protein"] == 70
        assert result["carbs"] == 130
        assert result["fat"] == 45

    @pytest.mark.asyncio
    async def test_excludes_non_ready_meals(self):
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=500,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.PROCESSING,
                nutrition=FakeNutrition(
                    calories=999,
                    macros=FakeNutritionMacros(protein=99, carbs=99, fat=99),
                ),
                created_at=datetime(2026, 3, 23, 13, 0, tzinfo=UTC),
            ),
        ]
        uow = FakeUoWAsync(meals=meals)
        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
        )
        assert result["calories"] == 500

    @pytest.mark.asyncio
    async def test_exclude_date_skips_meals_on_that_date(self):
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=500,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=700,
                    macros=FakeNutritionMacros(protein=40, carbs=80, fat=25),
                ),
                created_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            ),
        ]
        uow = FakeUoWAsync(meals=meals)
        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
            exclude_date=date(2026, 3, 25),
            user_timezone="UTC",
        )
        assert result["calories"] == 500

    @pytest.mark.asyncio
    async def test_exclude_dates_skips_multiple_dates(self):
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=500,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=600,
                    macros=FakeNutritionMacros(protein=35, carbs=60, fat=22),
                ),
                created_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=700,
                    macros=FakeNutritionMacros(protein=40, carbs=80, fat=25),
                ),
                created_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            ),
        ]
        uow = FakeUoWAsync(meals=meals)
        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
            exclude_dates=[date(2026, 3, 23), date(2026, 3, 25)],
            user_timezone="UTC",
        )
        # Only March 24 meal counted
        assert result["calories"] == 578

    @pytest.mark.asyncio
    async def test_end_date_excludes_future_meals(self):
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=500,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=4000,
                    macros=FakeNutritionMacros(protein=80, carbs=500, fat=180),
                ),
                created_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            ),
        ]
        uow = FakeUoWAsync(meals=meals)

        result = await WeeklyBudgetService.calculate_weekly_consumed_async(
            uow,
            "user-1",
            date(2026, 3, 23),
            end_date=date(2026, 3, 24),
            user_timezone="UTC",
        )

        assert result["calories"] == 500


class TestGetEffectiveAdjustedDailyAsync:
    """Test get_effective_adjusted_daily_async method."""

    @pytest.mark.asyncio
    async def test_returns_base_targets_when_no_logging(self):
        """Should return base targets when insufficient logging data."""
        from dataclasses import dataclass

        @dataclass
        class MockBudget:
            target_calories: float = 14000
            target_protein: float = 1050
            target_carbs: float = 1750
            target_fat: float = 490
            consumed_calories: float = 0
            consumed_protein: float = 0
            consumed_carbs: float = 0
            consumed_fat: float = 0
            remaining_calories: float = 14000
            remaining_protein: float = 1050
            remaining_carbs: float = 1750
            remaining_fat: float = 490

        mock_uow = Mock()
        mock_uow.cheat_days.find_by_user_and_date_range = AsyncMock(return_value=[])
        mock_uow.meals.get_daily_meal_counts = AsyncMock(return_value={})
        mock_uow.meals.find_by_date_range = AsyncMock(return_value=[])

        week_start = date(2026, 3, 9)
        target_date = date(2026, 3, 13)  # Friday, 4 days into week

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=mock_uow,
            user_id="user123",
            week_start=week_start,
            target_date=target_date,
            weekly_budget=MockBudget(),
            base_daily_cal=2000,
            base_daily_protein=150,
            base_daily_carbs=250,
            base_daily_fat=70,
            bmr=1600,
            user_timezone="UTC",
        )

        assert result.show_logging_prompt is True
        assert result.adjusted.calories > 0

    @pytest.mark.asyncio
    async def test_workout_credit_softens_next_day_adjustment(self):
        """2300 food - 200 movement behaves like 2100 weekly calories."""
        meal = _ready_meal(
            protein=100,
            carbs=250,
            fat=100,
            created_at=datetime(2026, 3, 9, 12, tzinfo=UTC),
        )

        mock_uow = Mock()
        mock_uow.cheat_days.find_by_user_and_date_range = AsyncMock(return_value=[])
        mock_uow.meals.get_daily_meal_counts = AsyncMock(
            return_value={date(2026, 3, 9): 1}
        )
        mock_uow.meals.find_by_date_range = AsyncMock(return_value=[meal])
        mock_uow.movement_entries = _FakeMovementEntries(
            [(datetime(2026, 3, 9, 18, tzinfo=UTC), 200.0, True)]
        )

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=mock_uow,
            user_id="user123",
            week_start=date(2026, 3, 9),
            target_date=date(2026, 3, 10),
            weekly_budget=WeeklyMacroBudget(
                weekly_budget_id="budget-1",
                user_id="user123",
                week_start_date=date(2026, 3, 9),
                target_calories=14000,
                target_protein=700,
                target_carbs=1750,
                target_fat=466.6667,
            ),
            base_daily_cal=2000,
            base_daily_protein=100,
            base_daily_carbs=250,
            base_daily_fat=66.6667,
            bmr=1600,
            user_timezone="UTC",
        )

        assert result.consumed_before_today["calories"] == 2100.0
        assert result.consumed_total["calories"] == 2100.0
        assert result.consumed_total["protein"] == 100
        assert result.consumed_total["carbs"] == 250
        assert result.consumed_total["fat"] == 100
        assert result.adjusted.calories == pytest.approx(1983.3, abs=1.0)

    # --- Ported from TestGetEffectiveAdjustedDaily (sync) — see scout matrix ---

    @pytest.mark.asyncio
    async def test_monday_first_day_returns_base(self):
        """Monday: no past days, remaining=7, returns base daily targets."""
        week_start = date(2026, 3, 23)  # Monday
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=[], daily_counts={})

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=week_start,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )
        assert result.show_logging_prompt is False
        assert result.skipped_days == 0
        assert result.logged_past_days == 0
        assert result.adjusted.remaining_days == 7

    @pytest.mark.asyncio
    async def test_midweek_with_logged_days_prorates(self):
        """Wednesday with Mon/Tue logged: prorates over (2 logged + 5 remaining) = 7 effective days."""
        week_start = date(2026, 3, 23)
        wednesday = date(2026, 3, 25)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=1800,
                    macros=FakeNutritionMacros(protein=70, carbs=250, fat=40),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=1800,
                    macros=FakeNutritionMacros(protein=70, carbs=250, fat=40),
                ),
                created_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1, date(2026, 3, 24): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=wednesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )
        assert result.logged_past_days == 2
        assert result.skipped_days == 0
        assert result.show_logging_prompt is False
        assert result.adjusted.remaining_days == 5

    @pytest.mark.asyncio
    async def test_midweek_fresh_no_meals_shows_logging_prompt(self):
        """Thursday fresh (0 meals, 0 logged days, past_days=3 >= 3): shows prompt."""
        week_start = date(2026, 3, 23)
        thursday = date(2026, 3, 26)  # 3 past days (Mon/Tue/Wed)
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=[], daily_counts={})

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=thursday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )
        # total_logged = 0 + 1 = 1 < MIN_LOGGED(3), past_days=3 >= 3 → prompt
        assert result.show_logging_prompt is True
        assert result.skipped_days == 3
        # When show_logging_prompt, returns base targets (remaining=7)
        assert result.adjusted.remaining_days == 7

    @pytest.mark.asyncio
    async def test_cheat_day_excluded_from_redistribution(self):
        """Cheat day consumption excluded from redistribution math."""
        week_start = date(2026, 3, 23)
        wednesday = date(2026, 3, 25)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=2000,
                    macros=FakeNutritionMacros(protein=70, carbs=250, fat=70),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=4000,
                    macros=FakeNutritionMacros(protein=100, carbs=500, fat=150),
                ),
                created_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1, date(2026, 3, 24): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result_with_cheat = (
            await WeeklyBudgetService.get_effective_adjusted_daily_async(
                uow=uow,
                user_id="user-1",
                week_start=week_start,
                target_date=wednesday,
                weekly_budget=budget,
                base_daily_cal=_BASE_CAL,
                base_daily_protein=_BASE_P,
                base_daily_carbs=_BASE_C,
                base_daily_fat=_BASE_F,
                bmr=_BMR,
                cheat_dates=[date(2026, 3, 24)],
            )
        )

        result_no_cheat = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=wednesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )

        leftover_split = (14000 - 6000) / 5
        floor = WeeklyBudgetService.calorie_safety_floor(_BASE_CAL, _BMR)
        assert leftover_split < floor
        assert result_with_cheat.adjusted.calories == pytest.approx(floor, abs=1)
        assert result_no_cheat.adjusted.calories == pytest.approx(floor, abs=1)
        # Macros may differ due to different redistribution paths
        assert result_with_cheat.adjusted.carbs != result_no_cheat.adjusted.carbs

    @pytest.mark.asyncio
    async def test_cheat_dates_none_auto_loads(self):
        """When cheat_dates=None, auto-loads from uow.cheat_days."""
        week_start = date(2026, 3, 23)
        wednesday = date(2026, 3, 25)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=2000,
                    macros=FakeNutritionMacros(protein=70, carbs=250, fat=70),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1}
        cheat_days = [FakeCheatDay(date=date(2026, 3, 24))]
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(
            meals=meals, daily_counts=daily_counts, cheat_days=cheat_days
        )

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=wednesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=None,
        )
        assert result.adjusted is not None

    @pytest.mark.asyncio
    async def test_sunday_last_day(self):
        """Sunday: remaining_days=1, all previous consumed."""
        week_start = date(2026, 3, 23)
        sunday = date(2026, 3, 29)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=2000,
                    macros=FakeNutritionMacros(protein=70, carbs=250, fat=70),
                ),
                created_at=datetime(2026, 3, 23 + i, 12, 0, tzinfo=UTC),
            )
            for i in range(6)
        ]
        daily_counts = {date(2026, 3, 23 + i): 1 for i in range(6)}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=sunday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )
        assert result.adjusted.remaining_days == 1
        assert result.logged_past_days == 6

    @pytest.mark.asyncio
    async def test_consumed_total_and_before_today_returned(self):
        """Verifies consumed_total and consumed_before_today populated."""
        week_start = date(2026, 3, 23)
        tuesday = date(2026, 3, 24)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=500,
                    macros=FakeNutritionMacros(protein=30, carbs=50, fat=20),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=700,
                    macros=FakeNutritionMacros(protein=40, carbs=80, fat=25),
                ),
                created_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=tuesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            user_timezone="UTC",
            cheat_dates=[],
        )
        assert result.consumed_total["calories"] == 1205
        assert result.consumed_before_today["calories"] == 500

    @pytest.mark.asyncio
    async def test_future_meals_do_not_affect_selected_date_adjustment(self):
        """Tuesday target uses Monday only; Wednesday planned meals are ignored."""
        week_start = date(2026, 3, 23)
        tuesday = date(2026, 3, 24)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=1000,
                    macros=FakeNutritionMacros(protein=50, carbs=120, fat=35),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=5000,
                    macros=FakeNutritionMacros(protein=100, carbs=600, fat=244),
                ),
                created_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=tuesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            user_timezone="UTC",
            cheat_dates=[],
        )

        assert result.consumed_before_today["calories"] == 995
        assert result.adjusted.calories == pytest.approx((14000 - 995) / 6, abs=2)

    @pytest.mark.asyncio
    async def test_overeating_leftover_split_holds_safety_floor(self):
        """Monday 8,000 must not smash Tuesday to leftover/days (1,000)."""
        week_start = date(2026, 3, 23)
        tuesday = date(2026, 3, 24)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=8000,
                    macros=FakeNutritionMacros(protein=200, carbs=900, fat=400),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=tuesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
        )

        leftover_split = (14000 - 8000) / 6
        floor = WeeklyBudgetService.calorie_safety_floor(_BASE_CAL, _BMR)
        assert leftover_split < floor
        assert result.adjusted.calories == pytest.approx(floor, abs=1)
        assert result.adjusted.protein == _BASE_P

    @pytest.mark.asyncio
    async def test_disabled_auto_adjust_keeps_fixed_base_daily(self):
        """Overeating must not change today's target when auto-adjust is off."""
        week_start = date(2026, 3, 23)
        tuesday = date(2026, 3, 24)
        meals = [
            FakeMeal(
                status=MealStatus.READY,
                nutrition=FakeNutrition(
                    calories=8000,
                    macros=FakeNutritionMacros(protein=200, carbs=900, fat=400),
                ),
                created_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
            ),
        ]
        daily_counts = {date(2026, 3, 23): 1}
        budget = _make_budget(week_start)
        uow = FakeUoWAsync(meals=meals, daily_counts=daily_counts)

        result = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow,
            user_id="user-1",
            week_start=week_start,
            target_date=tuesday,
            weekly_budget=budget,
            base_daily_cal=_BASE_CAL,
            base_daily_protein=_BASE_P,
            base_daily_carbs=_BASE_C,
            base_daily_fat=_BASE_F,
            bmr=_BMR,
            cheat_dates=[],
            auto_adjust=False,
        )

        assert result.adjusted.calories == pytest.approx(_BASE_CAL, abs=0.1)
        assert result.adjusted.protein == _BASE_P
        assert result.adjusted.carbs == pytest.approx(_BASE_C, abs=0.1)
        assert result.adjusted.fat == pytest.approx(_BASE_F, abs=0.1)
        assert result.adjusted.bmr_floor_active is False
        assert result.show_logging_prompt is False

