"""
Weekly budget service for redistribution logic and smart prompt detection.

Single source of truth for adjusted daily targets — used by:
- GetWeeklyBudgetQueryHandler (API)
- get_adjusted_daily_target (notification + suggestion)
"""

import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.domain.constants import WeeklyBudgetConstants
from src.domain.model.meal import MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.utils.timezone_utils import ensure_utc, get_zone_info

logger = logging.getLogger(__name__)


@dataclass
class AdjustedDailyTargets:
    """Adjusted daily targets based on weekly budget consumption."""

    calories: float
    carbs: float
    fat: float
    protein: float
    bmr_floor_active: bool
    remaining_days: int


@dataclass
class EffectiveAdjustedResult:
    """Rich result from get_effective_adjusted_daily with context for UI."""

    adjusted: AdjustedDailyTargets
    consumed_before_today: dict[str, float]
    consumed_total: dict[str, float]
    logged_past_days: int
    skipped_days: int
    show_logging_prompt: bool


@dataclass
class WeeklyEffectivePreload:
    """Preloaded weekly-consumed inputs for batch callers (e.g. precompute cron)."""

    logged_past_days: int
    consumed_total: dict[str, float]
    consumed_before_today: dict[str, float]
    consumed_for_redistribution: dict[str, float]


class WeeklyBudgetService:
    """Service for weekly budget calculations."""

    @staticmethod
    async def calculate_weekly_consumed_async(
        uow: Any,
        user_id: str,
        week_start: date,
        end_date: date | None = None,
        exclude_date: date | None = None,
        exclude_dates: list[date] | None = None,
        user_timezone: str | None = None,
    ) -> dict[str, float]:
        """Async version of calculate_weekly_consumed for AsyncUnitOfWork."""
        week_end = end_date or week_start + timedelta(days=6)
        tz = get_zone_info(user_timezone) if user_timezone else None

        meals = await uow.meals.find_by_date_range(
            user_id,
            week_start,
            week_end,
            user_timezone=user_timezone,
        )

        total_calories = 0.0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0

        exclude_dates_set = set(exclude_dates) if exclude_dates else set()
        for meal in meals:
            if meal.status == MealStatus.READY and meal.nutrition:
                if (end_date or exclude_date or exclude_dates_set) and meal.created_at:
                    aware_dt = ensure_utc(meal.created_at)
                    meal_local_date = (
                        aware_dt.astimezone(tz).date() if tz else aware_dt.date()
                    )
                    if end_date and meal_local_date > end_date:
                        continue
                    if exclude_date and meal_local_date == exclude_date:
                        continue
                    if meal_local_date in exclude_dates_set:
                        continue
                macros = meal.nutrition.macros
                total_protein += macros.protein or 0
                total_carbs += macros.carbs or 0
                total_fat += macros.fat or 0
                total_calories += effective_meal_calories(meal)

        movement_kcal = await WeeklyBudgetService._calculate_movement_kcal_async(
            uow=uow,
            user_id=user_id,
            week_start=week_start,
            end_date=week_end,
            exclude_date=exclude_date,
            exclude_dates=exclude_dates,
            user_timezone=user_timezone,
        )
        total_calories -= movement_kcal

        return {
            "calories": total_calories,
            "protein": total_protein,
            "carbs": total_carbs,
            "fat": total_fat,
        }

    @staticmethod
    async def _calculate_movement_kcal_async(
        uow: Any,
        user_id: str,
        week_start: date,
        end_date: date,
        exclude_date: date | None = None,
        exclude_dates: list[date] | None = None,
        user_timezone: str | None = None,
    ) -> float:
        """Sum included movement kcal for local dates in the weekly window."""
        if end_date < week_start:
            return 0.0

        movement_repo = vars(uow).get("movement_entries")
        if movement_repo is None:
            return 0.0

        sum_range = getattr(movement_repo, "sum_included_kcal_for_range", None)
        if sum_range is None:
            return 0.0

        excluded = set(exclude_dates) if exclude_dates else set()
        if exclude_date:
            excluded.add(exclude_date)

        if not excluded:
            start_utc, end_utc = WeeklyBudgetService._local_date_range_to_utc(
                week_start, end_date, user_timezone
            )
            return float(await sum_range(user_id, start_utc, end_utc) or 0.0)

        total = 0.0
        current = week_start
        while current <= end_date:
            if current not in excluded:
                start_utc, end_utc = WeeklyBudgetService._local_date_range_to_utc(
                    current, current, user_timezone
                )
                total += float(await sum_range(user_id, start_utc, end_utc) or 0.0)
            current += timedelta(days=1)
        return total

    @staticmethod
    def _local_date_range_to_utc(
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> tuple[datetime, datetime]:
        tz = get_zone_info(user_timezone or "UTC")
        start_local = datetime.combine(start_date, time.min, tzinfo=tz)
        end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
        return (
            start_local.astimezone(UTC),
            end_local.astimezone(UTC),
        )

    @staticmethod
    def _sum_movement_from_daily_map(
        movement_by_date: dict[date, float],
        week_start: date,
        end_date: date,
        exclude_date: date | None = None,
        exclude_dates: list[date] | None = None,
    ) -> float:
        excluded = set(exclude_dates) if exclude_dates else set()
        if exclude_date:
            excluded.add(exclude_date)
        total = 0.0
        current = week_start
        while current <= end_date:
            if current not in excluded:
                total += movement_by_date.get(current, 0.0)
            current += timedelta(days=1)
        return total

    @staticmethod
    def aggregate_weekly_consumed_from_meal_rows(
        meal_rows: list[tuple[datetime, float, float, float, float]],
        *,
        week_start: date,
        end_date: date | None = None,
        exclude_date: date | None = None,
        exclude_dates: list[date] | None = None,
        movement_by_date: dict[date, float] | None = None,
        user_timezone: str | None = None,
    ) -> dict[str, float]:
        """Sum READY-meal macros for a user from preloaded rows (batch precompute path)."""
        tz = get_zone_info(user_timezone) if user_timezone else None
        week_end = end_date or week_start + timedelta(days=6)
        exclude_dates_set = set(exclude_dates) if exclude_dates else set()

        total_calories = 0.0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0

        for created_at, protein, carbs, fat, fiber in meal_rows:
            aware_dt = ensure_utc(created_at)
            meal_local_date = (
                aware_dt.astimezone(tz).date() if tz else aware_dt.date()
            )
            if meal_local_date < week_start:
                continue
            if end_date and meal_local_date > end_date:
                continue
            if exclude_date and meal_local_date == exclude_date:
                continue
            if meal_local_date in exclude_dates_set:
                continue

            protein_val = protein or 0.0
            carbs_val = carbs or 0.0
            fat_val = fat or 0.0
            fiber_val = fiber or 0.0
            total_protein += protein_val
            total_carbs += carbs_val
            total_fat += fat_val
            total_calories += Macros(
                protein=protein_val,
                carbs=carbs_val,
                fat=fat_val,
                fiber=fiber_val,
            ).total_calories

        movement_kcal = WeeklyBudgetService._sum_movement_from_daily_map(
            movement_by_date or {},
            week_start,
            week_end,
            exclude_date=exclude_date,
            exclude_dates=exclude_dates,
        )
        total_calories -= movement_kcal

        return {
            "calories": total_calories,
            "protein": total_protein,
            "carbs": total_carbs,
            "fat": total_fat,
        }

    @staticmethod
    def build_weekly_effective_preload(
        *,
        meal_rows: list[tuple[datetime, float, float, float, float]],
        hydratable_dates: set[date],
        movement_by_date: dict[date, float],
        cheat_dates: list[date],
        week_start: date,
        target_date: date,
        user_timezone: str,
    ) -> WeeklyEffectivePreload:
        """Build per-user preload maps from batch-fetched rows (one timezone group)."""
        past_end = target_date - timedelta(days=1)
        past_days_count = (target_date - week_start).days
        past_cheat_dates = [d for d in cheat_dates if d < target_date]

        logged_past_days = 0
        if past_days_count > 0:
            logged_past_days = len(
                {
                    d
                    for d in hydratable_dates
                    if week_start <= d <= past_end
                }
            )

        consumed_total = WeeklyBudgetService.aggregate_weekly_consumed_from_meal_rows(
            meal_rows,
            week_start=week_start,
            movement_by_date=movement_by_date,
            user_timezone=user_timezone,
        )
        consumed_before_today = (
            WeeklyBudgetService.aggregate_weekly_consumed_from_meal_rows(
                meal_rows,
                week_start=week_start,
                end_date=past_end,
                movement_by_date=movement_by_date,
                user_timezone=user_timezone,
            )
        )
        if past_cheat_dates:
            consumed_for_redistribution = (
                WeeklyBudgetService.aggregate_weekly_consumed_from_meal_rows(
                    meal_rows,
                    week_start=week_start,
                    end_date=past_end,
                    exclude_dates=past_cheat_dates,
                    movement_by_date=movement_by_date,
                    user_timezone=user_timezone,
                )
            )
        else:
            consumed_for_redistribution = consumed_before_today

        return WeeklyEffectivePreload(
            logged_past_days=logged_past_days,
            consumed_total=consumed_total,
            consumed_before_today=consumed_before_today,
            consumed_for_redistribution=consumed_for_redistribution,
        )

    @staticmethod
    def _apply_effective_adjusted_policy(
        *,
        weekly_budget: Any,
        week_start: date,
        target_date: date,
        base_daily_cal: float,
        base_daily_protein: float,
        base_daily_carbs: float,
        base_daily_fat: float,
        bmr: float,
        cheat_dates: list[date],
        logged_past_days: int,
        consumed_total: dict[str, float],
        consumed_before_today: dict[str, float],
        consumed_for_redistribution: dict[str, float],
    ) -> EffectiveAdjustedResult:
        """Shared effective-adjusted policy for async and batch-preloaded callers."""
        calc = WeeklyBudgetService
        past_cheat_dates = [d for d in cheat_dates if d < target_date]
        past_cheat_count = len(past_cheat_dates)
        remaining_days = calc.calculate_remaining_days(week_start, target_date)

        past_days_count = (target_date - week_start).days
        skipped_days = 0
        show_logging_prompt = False
        if past_days_count > 0:
            skipped_days = past_days_count - logged_past_days
            total_logged = logged_past_days + 1
            if (
                total_logged < WeeklyBudgetConstants.MIN_LOGGED_DAYS_FOR_REDISTRIBUTION
                and past_days_count >= 3
            ):
                show_logging_prompt = True

        redistribution_logged_days = max(0, logged_past_days - past_cheat_count)

        if show_logging_prompt:
            adjusted = calc.calculate_adjusted_daily(
                replace(
                    weekly_budget,
                    consumed_calories=0,
                    consumed_protein=0,
                    consumed_carbs=0,
                    consumed_fat=0,
                ),
                standard_daily_calories=base_daily_cal,
                standard_daily_carbs=base_daily_carbs,
                standard_daily_fat=base_daily_fat,
                standard_daily_protein=base_daily_protein,
                bmr=bmr,
                remaining_days=7,
            )
        else:
            effective_week_days = redistribution_logged_days + remaining_days
            budget_for_adjustment = replace(
                weekly_budget,
                target_calories=base_daily_cal * effective_week_days,
                target_protein=base_daily_protein * effective_week_days,
                target_carbs=base_daily_carbs * effective_week_days,
                target_fat=base_daily_fat * effective_week_days,
                consumed_calories=consumed_for_redistribution["calories"],
                consumed_protein=consumed_for_redistribution["protein"],
                consumed_carbs=consumed_for_redistribution["carbs"],
                consumed_fat=consumed_for_redistribution["fat"],
            )
            adjusted = calc.calculate_adjusted_daily(
                budget_for_adjustment,
                standard_daily_calories=base_daily_cal,
                standard_daily_carbs=base_daily_carbs,
                standard_daily_fat=base_daily_fat,
                standard_daily_protein=base_daily_protein,
                bmr=bmr,
                remaining_days=remaining_days,
            )

        remaining_before_today = (
            weekly_budget.target_calories - consumed_before_today["calories"]
        )
        adjusted = calc.apply_leftover_budget_cap(
            adjusted,
            remaining_before_today=remaining_before_today,
            remaining_days=remaining_days,
            standard_daily_calories=base_daily_cal,
            bmr=bmr,
        )

        return EffectiveAdjustedResult(
            adjusted=adjusted,
            consumed_before_today=consumed_before_today,
            consumed_total=consumed_total,
            logged_past_days=logged_past_days,
            skipped_days=skipped_days,
            show_logging_prompt=show_logging_prompt,
        )

    @staticmethod
    async def get_effective_adjusted_daily_async(
        uow: Any,
        user_id: str,
        week_start: date,
        target_date: date,
        weekly_budget: Any,
        base_daily_cal: float,
        base_daily_protein: float,
        base_daily_carbs: float,
        base_daily_fat: float,
        bmr: float,
        user_timezone: str = "UTC",
        cheat_dates: list[date] | None = None,
        weekly_preload: WeeklyEffectivePreload | None = None,
    ) -> EffectiveAdjustedResult:
        """Async version of get_effective_adjusted_daily for AsyncUnitOfWork."""
        calc = WeeklyBudgetService

        if cheat_dates is None:
            cheat_day_records = await uow.cheat_days.find_by_user_and_date_range(
                user_id, week_start, week_start + timedelta(days=6)
            )
            all_cheat_dates = [cd.date for cd in cheat_day_records]
        else:
            all_cheat_dates = cheat_dates

        if weekly_preload is not None:
            return calc._apply_effective_adjusted_policy(
                weekly_budget=weekly_budget,
                week_start=week_start,
                target_date=target_date,
                base_daily_cal=base_daily_cal,
                base_daily_protein=base_daily_protein,
                base_daily_carbs=base_daily_carbs,
                base_daily_fat=base_daily_fat,
                bmr=bmr,
                cheat_dates=all_cheat_dates,
                logged_past_days=weekly_preload.logged_past_days,
                consumed_total=weekly_preload.consumed_total,
                consumed_before_today=weekly_preload.consumed_before_today,
                consumed_for_redistribution=weekly_preload.consumed_for_redistribution,
            )

        past_end = target_date - timedelta(days=1)
        past_days_count = (target_date - week_start).days
        logged_past_days = 0
        if past_days_count > 0:
            daily_counts = await uow.meals.get_daily_meal_counts(
                user_id,
                week_start,
                past_end,
                user_timezone=user_timezone,
            )
            logged_past_days = len(daily_counts)

        consumed_total = await calc.calculate_weekly_consumed_async(
            uow,
            user_id,
            week_start,
            user_timezone=user_timezone,
        )
        consumed_before_today = await calc.calculate_weekly_consumed_async(
            uow,
            user_id,
            week_start,
            end_date=past_end,
            user_timezone=user_timezone,
        )
        past_cheat_dates = [d for d in all_cheat_dates if d < target_date]
        if past_cheat_dates:
            consumed_for_redistribution = await calc.calculate_weekly_consumed_async(
                uow,
                user_id,
                week_start,
                end_date=past_end,
                exclude_dates=past_cheat_dates,
                user_timezone=user_timezone,
            )
        else:
            consumed_for_redistribution = consumed_before_today

        return calc._apply_effective_adjusted_policy(
            weekly_budget=weekly_budget,
            week_start=week_start,
            target_date=target_date,
            base_daily_cal=base_daily_cal,
            base_daily_protein=base_daily_protein,
            base_daily_carbs=base_daily_carbs,
            base_daily_fat=base_daily_fat,
            bmr=bmr,
            cheat_dates=all_cheat_dates,
            logged_past_days=logged_past_days,
            consumed_total=consumed_total,
            consumed_before_today=consumed_before_today,
            consumed_for_redistribution=consumed_for_redistribution,
        )

    @staticmethod
    def calculate_adjusted_daily(
        weekly_budget: WeeklyMacroBudget,
        standard_daily_calories: float,
        standard_daily_carbs: float,
        standard_daily_fat: float,
        standard_daily_protein: float,
        bmr: float,
        remaining_days: int,
    ) -> AdjustedDailyTargets:
        """Calculate adjusted daily targets based on remaining weekly budget.

        Pure math — no DB access. Uses budget's remaining_* properties.
        """
        if remaining_days <= 0:
            remaining_days = 1

        # Calculate BMR floor (80% of standard daily)
        bmr_floor = max(
            bmr, standard_daily_calories * WeeklyBudgetConstants.BMR_FLOOR_RATIO
        )

        # Redistribute remaining weekly budget
        remaining_calories = weekly_budget.remaining_calories
        remaining_carbs = weekly_budget.remaining_carbs
        remaining_fat = weekly_budget.remaining_fat

        calorie_target = remaining_calories / remaining_days

        # Deficit cap: never reduce more than MAX_DAILY_DEFICIT_RATIO below base.
        min_allowed = standard_daily_calories * (
            1 - WeeklyBudgetConstants.MAX_DAILY_DEFICIT_RATIO
        )
        max_allowed = standard_daily_calories * (
            1 + WeeklyBudgetConstants.MAX_DAILY_SURPLUS_RATIO
        )
        calorie_target = min(max(calorie_target, min_allowed), max_allowed)

        bmr_floor_active = False
        if calorie_target < bmr_floor:
            calorie_target = bmr_floor
            bmr_floor_active = True

        adjusted_carbs = remaining_carbs / remaining_days
        adjusted_fat = remaining_fat / remaining_days

        # Apply floors and ceilings to macros (prevents 0g fat, absurd carbs)
        floor = WeeklyBudgetConstants.MACRO_FLOOR_RATIO
        ceil = WeeklyBudgetConstants.MACRO_CEILING_RATIO
        adjusted_carbs = max(
            standard_daily_carbs * floor,
            min(adjusted_carbs, standard_daily_carbs * ceil),
        )
        adjusted_fat = max(
            standard_daily_fat * floor, min(adjusted_fat, standard_daily_fat * ceil)
        )

        # Protein stays fixed regardless of weekly consumption
        adjusted_protein = standard_daily_protein

        rounded_protein = round(adjusted_protein, 1)
        rounded_carbs = round(adjusted_carbs, 1)
        rounded_fat = round(adjusted_fat, 1)

        rounded_carbs, rounded_fat = WeeklyBudgetService._fit_carbs_fat_to_calories(
            calorie_target=calorie_target,
            protein=rounded_protein,
            carbs=rounded_carbs,
            fat=rounded_fat,
        )
        rounded_carbs = WeeklyBudgetService._clamp_macro(
            rounded_carbs,
            minimum=standard_daily_carbs * floor,
            maximum=standard_daily_carbs * ceil,
        )
        rounded_fat = WeeklyBudgetService._clamp_macro(
            rounded_fat,
            minimum=standard_daily_fat * floor,
            maximum=standard_daily_fat * ceil,
        )

        # Final calories still come from macros, but the macros are fitted to
        # the calorie-led redistribution target.
        adjusted_calories = (
            (rounded_protein * 4) + (rounded_carbs * 4) + (rounded_fat * 9)
        )
        while (
            not bmr_floor_active
            and adjusted_calories > max_allowed
            and (
                rounded_fat > standard_daily_fat * floor
                or rounded_carbs > standard_daily_carbs * floor
            )
        ):
            if rounded_fat > standard_daily_fat * floor:
                rounded_fat = round(rounded_fat - 0.1, 1)
            elif rounded_carbs > standard_daily_carbs * floor:
                rounded_carbs = round(rounded_carbs - 0.1, 1)
            adjusted_calories = (
                (rounded_protein * 4) + (rounded_carbs * 4) + (rounded_fat * 9)
            )

        return AdjustedDailyTargets(
            calories=round(adjusted_calories, 1),
            carbs=rounded_carbs,
            fat=rounded_fat,
            protein=rounded_protein,
            bmr_floor_active=bmr_floor_active,
            remaining_days=remaining_days,
        )

    @staticmethod
    def calorie_safety_floor(standard_daily_calories: float, bmr: float) -> float:
        """Lowest allowed adjusted day: deficit cap or BMR floor, whichever is higher."""
        bmr_floor = max(
            bmr, standard_daily_calories * WeeklyBudgetConstants.BMR_FLOOR_RATIO
        )
        deficit_floor = standard_daily_calories * (
            1 - WeeklyBudgetConstants.MAX_DAILY_DEFICIT_RATIO
        )
        return max(bmr_floor, deficit_floor)

    @staticmethod
    def apply_leftover_budget_cap(
        adjusted: AdjustedDailyTargets,
        *,
        remaining_before_today: float,
        remaining_days: int,
        standard_daily_calories: float,
        bmr: float,
    ) -> AdjustedDailyTargets:
        """Split leftover weekly calories, but never below the safety floor.

        Leftover / remaining days can still lower a day that is above the
        floor. It must not undo the deficit cap or BMR floor.
        """
        if remaining_days <= 0 or remaining_before_today <= 0:
            return adjusted

        leftover_daily = remaining_before_today / remaining_days
        if adjusted.calories <= leftover_daily:
            return adjusted

        floor = WeeklyBudgetService.calorie_safety_floor(
            standard_daily_calories, bmr
        )
        capped = max(leftover_daily, floor)
        if capped >= adjusted.calories:
            return adjusted

        scale = capped / adjusted.calories
        return AdjustedDailyTargets(
            calories=round(capped, 1),
            carbs=round(adjusted.carbs * scale, 1),
            fat=round(adjusted.fat * scale, 1),
            protein=adjusted.protein,
            bmr_floor_active=adjusted.bmr_floor_active,
            remaining_days=adjusted.remaining_days,
        )

    @staticmethod
    def _fit_carbs_fat_to_calories(
        *,
        calorie_target: float,
        protein: float,
        carbs: float,
        fat: float,
    ) -> tuple[float, float]:
        protein_calories = protein * 4
        non_protein_target = calorie_target - protein_calories
        non_protein_current = (carbs * 4) + (fat * 9)

        if non_protein_target <= 0 or non_protein_current <= 0:
            return carbs, fat

        scale = non_protein_target / non_protein_current
        return round(carbs * scale, 1), round(fat * scale, 1)

    @staticmethod
    def _clamp_macro(value: float, *, minimum: float, maximum: float) -> float:
        return round(max(minimum, min(value, maximum)), 1)

    @staticmethod
    def should_suggest_cheat_day(
        daily_consumed: float,
        daily_target: float,
        is_already_cheat_day: bool,
    ) -> bool:
        """Suggest marking today as cheat day when consumed > target."""
        if is_already_cheat_day:
            return False
        threshold = daily_target * WeeklyBudgetConstants.SMART_PROMPT_THRESHOLD
        return daily_consumed > threshold

    @staticmethod
    def calculate_remaining_days(week_start: date, target_date: date) -> int:
        """Calculate remaining days in the week from target date.

        Returns number of days remaining (including target date).
        """
        week_end = week_start + timedelta(days=6)
        if target_date > week_end:
            return 0
        return (week_end - target_date).days + 1
