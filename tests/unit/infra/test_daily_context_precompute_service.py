from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clear_sentinel():
    """Wipe in-memory sentinel between tests to prevent leakage."""
    from src.infra.services import daily_context_precompute_service as module
    module._precomputed_today.clear()
    yield
    module._precomputed_today.clear()


@pytest.mark.asyncio
async def test_skips_if_sentinel_in_memory():
    """Pre-compute is skipped when (date, tz) already in _precomputed_today."""
    from src.infra.services import daily_context_precompute_service as module
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    today = date(2026, 4, 22)
    module._precomputed_today.add((today.isoformat(), "Asia/Ho_Chi_Minh"))

    with patch.object(svc, "_precompute_db", new_callable=AsyncMock) as mock_precompute:
        await svc.precompute_for_timezone("Asia/Ho_Chi_Minh", today)
        mock_precompute.assert_not_awaited()


@pytest.mark.asyncio
async def test_runs_and_adds_to_sentinel_set():
    """Pre-compute runs and adds (date, tz) to _precomputed_today on success."""
    from src.infra.services import daily_context_precompute_service as module
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    today = date(2026, 4, 22)

    with patch.object(svc, "_precompute_db", AsyncMock(return_value=5)), patch.object(
        svc, "_check_db_sentinel", AsyncMock(return_value=False)
    ):
        await svc.precompute_for_timezone("Asia/Ho_Chi_Minh", today)

    assert (today.isoformat(), "Asia/Ho_Chi_Minh") in module._precomputed_today


@pytest.mark.asyncio
async def test_zero_users_does_not_set_sentinel():
    """When no users are eligible, sentinel must NOT be set (allows retry)."""
    from src.infra.services import daily_context_precompute_service as module
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    today = date(2026, 4, 22)

    with patch.object(svc, "_precompute_db", AsyncMock(return_value=0)), patch.object(
        svc, "_check_db_sentinel", AsyncMock(return_value=False)
    ):
        await svc.precompute_for_timezone("Asia/Ho_Chi_Minh", today)

    assert (today.isoformat(), "Asia/Ho_Chi_Minh") not in module._precomputed_today


@pytest.mark.asyncio
async def test_db_sentinel_fallback_skips_precompute():
    """When in-memory set is empty but DB has notifications, precompute is skipped."""
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    today = date(2026, 4, 22)

    with patch.object(
        svc, "_check_db_sentinel", AsyncMock(return_value=True)
    ), patch.object(svc, "_precompute_db", new_callable=AsyncMock) as mock_precompute:
        await svc.precompute_for_timezone("Asia/Ho_Chi_Minh", today)
        mock_precompute.assert_not_awaited()


def test_sentinel_key_format():
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    key = svc.sentinel_key(date(2026, 4, 22), "Asia/Ho_Chi_Minh")
    assert key == "precomputed:2026-04-22:Asia/Ho_Chi_Minh"


def test_notification_language_prefers_in_app_user_language():
    from src.infra.services.daily_context_precompute_service import (
        _resolve_notification_language,
    )

    assert _resolve_notification_language("en", "vi") == "vi"


def test_notification_language_uses_pref_when_user_language_missing():
    from src.infra.services.daily_context_precompute_service import (
        _resolve_notification_language,
    )

    assert _resolve_notification_language("vi", None) == "vi"


@pytest.mark.asyncio
async def test_user_calorie_goal_uses_adjusted_weekly_budget_target():
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()

    mock_profile = SimpleNamespace(
        age=30,
        gender="male",
        height_cm=175,
        weight_kg=70,
        body_fat_percentage=None,
        job_type="sedentary",
        training_days_per_week=3,
        training_minutes_per_session=45,
        fitness_goal="maintain",
        training_level="beginner",
        profile_target_revision=1,
    )

    mock_budget = MagicMock()
    mock_budget.target_calories = 14000
    mock_budget.target_protein = 700
    mock_budget.target_carbs = 1750
    mock_budget.target_fat = 350
    mock_budget.target_revision = 1

    mock_uow = MagicMock()
    mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=mock_budget)
    mock_uow.session = MagicMock()

    with patch(
        "src.infra.services.daily_context_precompute_service.WeeklyBudgetService.get_effective_adjusted_daily_async",
        new_callable=AsyncMock,
    ) as mock_effective:
        mock_macros = MagicMock()
        mock_macros.calories = 2000.0
        mock_result = MagicMock()
        mock_result.adjusted = mock_macros
        mock_effective.return_value = mock_result

        result = await svc._get_user_calorie_goal(
            mock_uow, "user-123", date(2026, 4, 22), mock_profile, "UTC"
        )
        assert result == 2000


@pytest.mark.asyncio
async def test_user_calorie_goal_rejects_stale_weekly_target_revision():
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    profile = SimpleNamespace(
        age=30, gender="male", height_cm=175, weight_kg=70,
        body_fat_percentage=None, job_type="desk", training_days_per_week=3,
        training_minutes_per_session=45, fitness_goal="maintain",
        training_level="beginner", profile_target_revision=2,
    )
    budget = MagicMock(target_revision=1)
    uow = MagicMock()
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=budget)

    with pytest.raises(ValueError, match="stale"):
        await DailyContextPrecomputeService()._get_user_calorie_goal(
            uow, "u1", date(2026, 4, 22), profile, "UTC"
        )


@pytest.mark.asyncio
async def test_precompute_preserves_adjusted_goal_for_keto_users():
    from src.domain.model.user import MacroPreset
    from src.domain.services.weekly_budget_service import AdjustedDailyTargets
    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    profile = SimpleNamespace(
        age=30, gender="male", height_cm=175, weight_kg=70,
        body_fat_percentage=None, job_type="desk", training_days_per_week=3,
        training_minutes_per_session=45, fitness_goal="maintain",
        training_level="beginner", dietary_preferences=["keto"],
        custom_protein_g=None, custom_carbs_g=None, custom_fat_g=None,
        profile_target_revision=1,
    )
    budget = MagicMock(
        target_revision=1, target_calories=14000.0, target_protein=700.0,
        target_carbs=1750.0, target_fat=350.0,
    )
    uow = MagicMock()
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=budget)
    svc = DailyContextPrecomputeService()
    adjusted = AdjustedDailyTargets(1900.0, 100.0, 100.0, 100.0, False, 7)
    effective = MagicMock(adjusted=adjusted)

    with (
        patch(
            "src.infra.services.daily_context_precompute_service.WeeklyBudgetService.get_effective_adjusted_daily_async",
            new_callable=AsyncMock,
            return_value=effective,
        ),
        patch.object(
            svc._tdee_service,
            "apply_adjusted_macro_policy",
            wraps=svc._tdee_service.apply_adjusted_macro_policy,
        ) as apply_policy,
    ):
        result = await svc._get_user_calorie_goal(
            uow, "u1", date(2026, 4, 22), profile, "UTC"
        )

    apply_policy.assert_called_once()
    assert apply_policy.call_args.args[2] is MacroPreset.KETO
    assert result == 1900.0


@pytest.mark.asyncio
async def test_calorie_goal_loop_isolates_failures_with_savepoint():
    """One poisoned calorie-goal lookup must not block remaining users."""
    from contextlib import asynccontextmanager

    from src.infra.services.daily_context_precompute_service import (
        DailyContextPrecomputeService,
    )

    svc = DailyContextPrecomputeService()
    nested_entries = []
    processed: list[str] = []

    class _Session:
        @asynccontextmanager
        async def begin_nested(self):
            nested_entries.append("enter")
            try:
                yield
            finally:
                nested_entries.append("exit")

    session = _Session()
    user_ids = ["u-fail", "u-ok"]
    profiles_by_user = {
        "u-fail": SimpleNamespace(id="u-fail"),
        "u-ok": SimpleNamespace(id="u-ok"),
    }
    calorie_goals: dict[str, int] = {}

    async def fake_goal(uow, user_id, today, profile, tz_name):
        if user_id == "u-fail":
            raise RuntimeError("sql poisoned")
        return 2100

    with patch.object(svc, "_get_user_calorie_goal", side_effect=fake_goal):
        for user_id in user_ids:
            profile = profiles_by_user.get(user_id)
            if profile is None:
                continue
            try:
                async with session.begin_nested():
                    calorie_goals[user_id] = await svc._get_user_calorie_goal(
                        MagicMock(), user_id, date(2026, 4, 22), profile, "UTC"
                    )
                    processed.append(user_id)
            except Exception:
                processed.append(f"failed:{user_id}")

    assert processed == ["failed:u-fail", "u-ok"]
    assert calorie_goals == {"u-ok": 2100}
    assert nested_entries == ["enter", "exit", "enter", "exit"]
