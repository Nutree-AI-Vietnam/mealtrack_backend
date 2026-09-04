"""
Handler for getting weekly macro budget status.
"""

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from src.api.exceptions import ExternalServiceException
from src.app.events.base import EventHandler, handles
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.constants import WeeklyBudgetConstants
from src.domain.model.user import MacroPreset, MacroTargets
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.services.tdee_service import TdeeCalculationService
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import (
    get_user_monday,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetWeeklyBudgetQuery)
class GetWeeklyBudgetQueryHandler(EventHandler[GetWeeklyBudgetQuery, dict[str, Any]]):
    """Handler for getting weekly macro budget status."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        cache_service: CachePort | None = None,
    ):
        self.uow = uow
        self.cache_service = cache_service

    async def handle(self, query: GetWeeklyBudgetQuery) -> dict[str, Any]:
        """Handle getting weekly budget status."""
        # TDEE FIRST — behind Redis cache, opens its own short-lived UoW(s).
        # Must complete before the weekly-budget UoW below so we never nest
        # connection checkouts (pool deadlock under concurrent app opens).
        tdee_result = await self._resolve_tdee(
            query.user_id, persist=not query.read_only
        )
        profile_revision = int(tdee_result["profile_target_revision"])
        target_policy = self._policy_from_tdee(tdee_result)

        uow = self.uow or AsyncUnitOfWork()
        async with uow:
            try:
                # Resolve user timezone (DB → X-Timezone header → UTC)
                user_tz_str = await resolve_user_timezone_async(
                    query.user_id, uow, query.header_timezone
                )
                user_tz = get_zone_info(user_tz_str)

                # Default to today in USER's timezone (not server's UTC)
                if query.target_date:
                    target_date = query.target_date
                else:
                    target_date = datetime.now(user_tz).date()

                # target_date is already a local date — no timezone re-lookup needed
                week_start = get_user_monday(target_date, query.user_id)

                # Cache check uses target_date because adjusted targets and
                # remaining days differ across dates in the same week.
                cache_key, ttl = CacheKeys.weekly_budget(
                    query.user_id, week_start, target_date
                )
                auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
                    await uow.users.get_weekly_auto_adjust(query.user_id)
                )
                if self.cache_service:
                    cached = await self.cache_service.get_json(cache_key)
                    if (
                        cached is not None
                        and cached.get("profile_target_revision") == profile_revision
                        and WeeklyBudgetService.auto_adjust_enabled(
                            cached.get("weekly_auto_adjust", True)
                        )
                        == auto_adjust
                    ):
                        return cached

                # Find or create weekly budget
                weekly_budget = await uow.weekly_budgets.find_by_user_and_week(
                    query.user_id, week_start
                )

                if not weekly_budget:
                    # Lazy init: create weekly budget from pre-resolved TDEE
                    weekly_budget, bmr = await self._create_weekly_budget(
                        uow,
                        query.user_id,
                        week_start,
                        target_date,
                        tdee_result=tdee_result,
                        persist=not query.read_only,
                    )
                else:
                    # Check if targets are stale and sync if needed
                    weekly_budget, bmr = await self._sync_targets_if_stale(
                        uow,
                        weekly_budget,
                        query.user_id,
                        tdee_result=tdee_result,
                        persist=not query.read_only,
                    )
                if weekly_budget.target_revision != profile_revision:
                    weekly_budget, bmr = await self._sync_targets_if_stale(
                        uow,
                        weekly_budget,
                        query.user_id,
                        tdee_result=tdee_result,
                        persist=not query.read_only,
                    )

                # Load cheat days for this week (pre-loaded, passed to shared method)
                cheat_days = await uow.cheat_days.find_by_user_and_date_range(
                    query.user_id, week_start, week_start + timedelta(days=6)
                )
                past_cheat_dates = [
                    cd.date for cd in cheat_days if cd.date < target_date
                ]
                is_today_cheat = any(cd.date == target_date for cd in cheat_days)

                # Base daily targets
                base_daily_cal = weekly_budget.target_calories / 7
                base_daily_carbs = weekly_budget.target_carbs / 7
                base_daily_fat = weekly_budget.target_fat / 7
                base_daily_protein = weekly_budget.target_protein / 7

                effective = (
                    await WeeklyBudgetService.get_effective_adjusted_daily_async(
                        uow=uow,
                        user_id=query.user_id,
                        week_start=week_start,
                        target_date=target_date,
                        weekly_budget=weekly_budget,
                        base_daily_cal=base_daily_cal,
                        base_daily_protein=base_daily_protein,
                        base_daily_carbs=base_daily_carbs,
                        base_daily_fat=base_daily_fat,
                        bmr=bmr,
                        user_timezone=user_tz_str,
                        cheat_dates=past_cheat_dates,
                        auto_adjust=auto_adjust,
                    )
                )
                adjusted = effective.adjusted
                adjusted = self._apply_target_policy(
                    adjusted,
                    target_policy,
                )
                consumed_before_today = effective.consumed_before_today
                consumed = effective.consumed_total
                skipped_days = effective.skipped_days
                show_logging_prompt = effective.show_logging_prompt
                logged_past_days = effective.logged_past_days
                remaining_days = adjusted.remaining_days

                # Update budget consumed values in DB from fresh meal data
                weekly_budget.consumed_calories = consumed["calories"]
                weekly_budget.consumed_protein = consumed["protein"]
                weekly_budget.consumed_carbs = consumed["carbs"]
                weekly_budget.consumed_fat = consumed["fat"]
                if not query.read_only:
                    await uow.weekly_budgets.update(weekly_budget)

                # --- Tomorrow Preview ---
                # Shows real impact of today's consumption on tomorrow's target.
                preview_data: dict[str, Any] = {}
                today_consumed_cal = (
                    consumed["calories"] - consumed_before_today["calories"]
                )
                logger.info(
                    f"Preview check: remaining={remaining_days}, prompt={show_logging_prompt}, "
                    f"today_cal={today_consumed_cal:.0f}, base={base_daily_cal:.0f}, "
                    f"consumed_total={consumed['calories']:.0f}, consumed_before={consumed_before_today['calories']:.0f}, "
                    f"logged_past={logged_past_days}, skipped={skipped_days}, "
                    f"cheat_today={is_today_cheat}, cheat_past={len(past_cheat_dates)}"
                )
                if (
                    auto_adjust
                    and remaining_days > 1
                    and not show_logging_prompt
                    and today_consumed_cal > 0
                ):
                    # Preview uses original consumed data (cheat days included) for real impact
                    tomorrow_remaining = remaining_days - 1
                    consumed_including_today = consumed.copy()
                    effective_days_tomorrow = (
                        logged_past_days + 1 + tomorrow_remaining
                    )  # +1 = today
                    prorated_tomorrow_cal = base_daily_cal * effective_days_tomorrow
                    prorated_tomorrow_carbs = base_daily_carbs * effective_days_tomorrow
                    prorated_tomorrow_fat = base_daily_fat * effective_days_tomorrow
                    prorated_tomorrow_protein = (
                        base_daily_protein * effective_days_tomorrow
                    )

                    tomorrow_budget = replace(
                        weekly_budget,
                        target_calories=prorated_tomorrow_cal,
                        target_protein=prorated_tomorrow_protein,
                        target_carbs=prorated_tomorrow_carbs,
                        target_fat=prorated_tomorrow_fat,
                        consumed_calories=consumed_including_today["calories"],
                        consumed_protein=consumed_including_today["protein"],
                        consumed_carbs=consumed_including_today["carbs"],
                        consumed_fat=consumed_including_today["fat"],
                    )
                    tomorrow_adjusted = WeeklyBudgetService.calculate_adjusted_daily(
                        tomorrow_budget,
                        standard_daily_calories=base_daily_cal,
                        standard_daily_carbs=base_daily_carbs,
                        standard_daily_fat=base_daily_fat,
                        standard_daily_protein=base_daily_protein,
                        bmr=bmr,
                        remaining_days=tomorrow_remaining,
                    )
                    policy = target_policy
                    tomorrow_adjusted = self._apply_target_policy(
                        tomorrow_adjusted, policy
                    )

                    actual_remaining_after_today = (
                        weekly_budget.target_calories
                        - consumed_including_today["calories"]
                    )
                    tomorrow_adjusted = WeeklyBudgetService.apply_leftover_budget_cap(
                        tomorrow_adjusted,
                        remaining_before_today=actual_remaining_after_today,
                        remaining_days=tomorrow_remaining,
                        standard_daily_calories=base_daily_cal,
                        bmr=bmr,
                    )
                    tomorrow_adjusted = self._apply_target_policy(
                        tomorrow_adjusted, policy
                    )

                    deviation = abs(tomorrow_adjusted.calories - base_daily_cal) / max(
                        base_daily_cal, 1
                    )
                    logger.info(
                        f"Preview deviation: {deviation:.4f} (threshold={WeeklyBudgetConstants.PREVIEW_DEVIATION_THRESHOLD}), "
                        f"tomorrow_cal={tomorrow_adjusted.calories:.1f}, effective_days={effective_days_tomorrow}"
                    )
                    # Always send preview when meals logged today;
                    # mobile shows expanded (with delta badge) or just the projected number
                    direction = (
                        "over" if today_consumed_cal > adjusted.calories else "under"
                    )
                    preview_data = {
                        "preview_tomorrow_calories": tomorrow_adjusted.calories,
                        "preview_tomorrow_protein": tomorrow_adjusted.protein,
                        "preview_tomorrow_carbs": tomorrow_adjusted.carbs,
                        "preview_tomorrow_fat": tomorrow_adjusted.fat,
                        "preview_direction": direction,
                        "preview_delta": int(
                            abs(tomorrow_adjusted.calories - adjusted.calories)
                        ),
                        "preview_today_delta": int(
                            abs(today_consumed_cal - adjusted.calories)
                        ),
                    }

                # Derive remaining calories directly from target - consumed (negatives flow through)
                derived_remaining_cal = (
                    weekly_budget.target_calories - weekly_budget.consumed_calories
                )

                result = {
                    "week_start_date": week_start.isoformat(),
                    "target_calories": weekly_budget.target_calories,
                    "target_protein": weekly_budget.target_protein,
                    "target_carbs": weekly_budget.target_carbs,
                    "target_fat": weekly_budget.target_fat,
                    "consumed_calories": weekly_budget.consumed_calories,
                    "consumed_protein": weekly_budget.consumed_protein,
                    "consumed_carbs": weekly_budget.consumed_carbs,
                    "consumed_fat": weekly_budget.consumed_fat,
                    "remaining_calories": round(derived_remaining_cal, 1),
                    "remaining_protein": weekly_budget.remaining_protein,
                    "remaining_carbs": weekly_budget.remaining_carbs,
                    "remaining_fat": weekly_budget.remaining_fat,
                    "adjusted_daily_calories": adjusted.calories,
                    "adjusted_daily_carbs": adjusted.carbs,
                    "adjusted_daily_fat": adjusted.fat,
                    "daily_protein": adjusted.protein,
                    "remaining_days": remaining_days,
                    "profile_target_revision": profile_revision,
                    "target_revision": weekly_budget.target_revision,
                    "bmr_floor_active": adjusted.bmr_floor_active,
                    "weekly_auto_adjust": auto_adjust,
                    "cheat_days": [cd.date.isoformat() for cd in cheat_days],
                    "skipped_days": skipped_days,
                    "show_logging_prompt": show_logging_prompt,
                    **preview_data,
                }

                if self.cache_service:
                    await self.cache_service.set_json(
                        cache_key,
                        result,
                        ttl,
                        revision_field="profile_target_revision",
                    )

                return result

            except Exception:
                raise

    async def _resolve_tdee(self, user_id: str, *, persist: bool) -> dict[str, Any]:
        """Resolve authoritative TDEE outside any caller-held UoW."""
        try:
            from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
                GetUserTdeeQueryHandler,
            )
            from src.app.queries.tdee import GetUserTdeeQuery

            return await GetUserTdeeQueryHandler(
                cache_service=self.cache_service
            ).handle(GetUserTdeeQuery(user_id=user_id))
        except Exception as exc:
            raise ExternalServiceException(
                "Authoritative target calculation is unavailable",
                "target_unavailable" if persist else "target_service_unavailable",
            ) from exc

    @staticmethod
    def _policy_from_tdee(tdee_result: dict[str, Any]) -> tuple[MacroPreset, bool]:
        return MacroPreset(tdee_result["macro_preset"]), bool(tdee_result["is_custom"])

    @staticmethod
    def _apply_target_policy(adjusted, policy: tuple[MacroPreset, bool]):
        preset, is_custom = policy
        targets = TdeeCalculationService.apply_adjusted_macro_policy(
            adjusted.calories,
            MacroTargets(
                calories=adjusted.calories,
                protein=adjusted.protein,
                carbs=adjusted.carbs,
                fat=adjusted.fat,
            ),
            preset,
            is_custom,
        )
        return replace(
            adjusted,
            calories=targets.calories,
            protein=targets.protein,
            carbs=targets.carbs,
            fat=targets.fat,
        )

    async def _create_weekly_budget(
        self,
        uow: AsyncUnitOfWork,
        user_id: str,
        week_start: date,
        target_date: date,
        *,
        tdee_result: dict[str, Any],
        persist: bool = True,
    ) -> tuple[WeeklyMacroBudget, float]:
        """Create a new weekly budget for the user. Returns (budget, bmr)."""
        import uuid

        daily_macros = tdee_result["macros"]
        bmr = tdee_result["bmr"]
        target_revision = tdee_result.get("profile_target_revision", 1)
        weekly_targets = self._weekly_targets_from_daily_macros(daily_macros)

        # Create domain object
        budget = WeeklyMacroBudget(
            weekly_budget_id=str(uuid.uuid4()),
            user_id=user_id,
            week_start_date=week_start,
            **weekly_targets,
            target_revision=target_revision,
        )

        # Save to DB
        if persist:
            await uow.weekly_budgets.create(budget)

        return budget, bmr

    async def _sync_targets_if_stale(
        self,
        uow: AsyncUnitOfWork,
        weekly_budget: WeeklyMacroBudget,
        user_id: str,
        *,
        tdee_result: dict[str, Any],
        persist: bool = True,
    ) -> tuple[WeeklyMacroBudget, float]:
        """Check if weekly targets match current TDEE; update if stale. Returns (budget, bmr)."""
        daily_macros = tdee_result["macros"]
        bmr = tdee_result["bmr"]
        target_revision = tdee_result.get(
            "profile_target_revision", weekly_budget.target_revision
        )

        if daily_macros is None:
            return weekly_budget, bmr

        expected_targets = self._weekly_targets_from_daily_macros(daily_macros)
        current_targets = {
            "target_calories": weekly_budget.target_calories,
            "target_protein": weekly_budget.target_protein,
            "target_carbs": weekly_budget.target_carbs,
            "target_fat": weekly_budget.target_fat,
        }

        targets_changed = any(
            abs(expected_targets[key] - current_targets[key])
            / max(abs(current_targets[key]), 1)
            > 0.01
            for key in expected_targets
        )

        if targets_changed or weekly_budget.target_revision != target_revision:
            weekly_budget.target_calories = expected_targets["target_calories"]
            weekly_budget.target_protein = expected_targets["target_protein"]
            weekly_budget.target_carbs = expected_targets["target_carbs"]
            weekly_budget.target_fat = expected_targets["target_fat"]
            weekly_budget.target_revision = target_revision
            if persist:
                await uow.weekly_budgets.update(weekly_budget)
            logger.info("Updated stale weekly nutrition targets for user")

        return weekly_budget, bmr

    @staticmethod
    def _weekly_targets_from_daily_macros(
        daily_macros: dict[str, float],
    ) -> dict[str, float]:
        """Scale canonical daily macro grams and derive weekly calories from them."""
        target_protein = round(daily_macros["protein"] * 7, 1)
        target_carbs = round(daily_macros["carbs"] * 7, 1)
        target_fat = round(daily_macros["fat"] * 7, 1)
        return {
            "target_calories": round(
                target_protein * 4 + target_carbs * 4 + target_fat * 9,
                1,
            ),
            "target_protein": target_protein,
            "target_carbs": target_carbs,
            "target_fat": target_fat,
        }
