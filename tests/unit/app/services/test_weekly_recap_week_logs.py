"""Reading one user's week of logs for their weekly recap."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.fixtures.fakes.fake_weekly_recap_repository import (
    FakeWeeklyRecapRepository,
)

from src.app.services.weekly_recap_week_logs import load_week_logs, sign_up_day
from src.domain.model.hydration.hydration_entry import HydrationEntry
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.movement.movement_entry import MovementEntry
from src.domain.model.notification import NotificationPreferences
from src.domain.model.nutrition import Macros, Nutrition
from src.domain.model.weekly_recap import WeeklyRecap
from src.domain.model.weight.weight_entry import WeightEntry

TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)
MONDAY = date(2026, 10, 5)
SUNDAY = date(2026, 10, 11)
USER = "11111111-1111-1111-1111-111111111111"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
PROFILE = SimpleNamespace(fitness_goal="cut")


def _utc(day: int, hour: int) -> datetime:
    local = datetime.combine(MONDAY + timedelta(days=day), time(hour), TZ)
    return local.astimezone(UTC)


def _meal(day: int, hour: int, *, source: str = "scanner") -> Meal:
    created = _utc(day, hour)
    return Meal(
        meal_id=str(uuid4()),
        user_id=USER,
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Phở bò",
        nutrition=Nutrition(macros=Macros(protein=20.0, carbs=50.0, fat=10.0)),
        ready_at=created,
        source=source,
    )


def _move(day: int) -> MovementEntry:
    return MovementEntry(
        user_id=USER,
        activity_name="Gym",
        duration_min=45,
        kcal_burned=300.0,
        intensity="moderate",
        logged_at=_utc(day, 18),
        activity_id="gym_strength",
    )


def _drink(day: int) -> HydrationEntry:
    return HydrationEntry(
        user_id=USER,
        drink_name_snapshot="Trà sữa",
        volume_ml=500,
        credited_ml=400,
        logged_at=_utc(day, 15),
        carbs_g=30.0,
        sugar_g=25.0,
    )


def _weigh(day: int, kg: float) -> WeightEntry:
    return WeightEntry(user_id=USER, weight_kg=kg, recorded_at=_utc(day, 7))


def _recap(week_start: date, case_id: str) -> WeeklyRecap:
    return WeeklyRecap(USER, week_start, case_id, "en", stats={}, payload={})


class _Meals:
    def __init__(self, meals, log_dates):
        self._meals = list(meals)
        self._log_dates = list(log_dates)
        self.range_args = None

    async def get_dates_with_meals(self, user_id, user_timezone=None):
        assert (user_id, user_timezone) == (USER, TZ_NAME)
        return self._log_dates

    async def find_by_date_range(
        self,
        user_id,
        start_date,
        end_date,
        limit=500,
        user_timezone=None,
        projection=None,
    ):
        self.range_args = (
            user_id,
            start_date,
            end_date,
            limit,
            user_timezone,
            projection,
        )
        return self._meals


class _Entries:
    """Hands back its entries and remembers what it was asked for."""

    def __init__(self, entries):
        self._entries = list(entries)
        self.args = None
        self.kwargs = None

    async def _find(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        return self._entries

    find_by_user_and_logged_range = _find
    find_by_recorded_range = _find
    find_by_date_range = _find


class _Users:
    def __init__(self, profile):
        self._profile = profile
        self.asked = None

    async def get_profile(self, user_id):
        self.asked = user_id
        return self._profile


class _Notifications:
    def __init__(self, prefs):
        self._prefs = prefs
        self.asked = None

    async def find_notification_preferences_by_user(self, user_id):
        self.asked = user_id
        return self._prefs


class _Uow:
    def __init__(
        self,
        meals,
        *,
        log_dates=(MONDAY,),
        movements=(),
        weights=(),
        drinks=(),
        profile=PROFILE,
        recaps=(),
        prefs=None,
    ):
        self.meals = _Meals(meals, log_dates)
        self.movement_entries = _Entries(movements)
        self.weight_entries = _Entries(weights)
        self.hydration_entries = _Entries(drinks)
        self.users = _Users(profile)
        self.weekly_recaps = FakeWeeklyRecapRepository(recaps)
        self.notifications = _Notifications(prefs)


async def _load(uow: _Uow, created_at: datetime | None = LONG_AGO):
    return await load_week_logs(uow, USER, MONDAY, TZ_NAME, created_at, 3)


@pytest.mark.asyncio
async def test_load_week_logs_reads_the_week_in_local_time():
    log_dates = [MONDAY + timedelta(days=2), MONDAY, MONDAY - timedelta(days=1)]
    uow = _Uow(
        [_meal(2, 19), _meal(-1, 20), _meal(0, 8)],
        log_dates=log_dates,
        movements=[_move(-7), _move(3)],
        weights=[_weigh(-3, 80.0), _weigh(1, 79.0), _weigh(6, 78.6)],
        drinks=[_drink(4)],
        recaps=[
            _recap(MONDAY - timedelta(days=7), "perfect"),
            _recap(MONDAY, "steady"),
        ],
    )

    logs = await _load(uow)

    assert logs is not None
    assert [(meal.day, meal.logged_at.hour) for meal in logs.meals] == [(0, 8), (2, 19)]
    assert (logs.tz, logs.created, logs.goal) == (TZ, date(2026, 1, 1), "cut")
    assert logs.profile is PROFILE
    assert logs.log_dates == tuple(log_dates)
    assert [entry.logged_at for entry in logs.movements] == [_utc(-7, 18), _utc(3, 18)]
    assert [entry.logged_at for entry in logs.drinks] == [_utc(4, 15)]
    assert [entry.weight_kg for entry in logs.weights_this_week] == [79.0, 78.6]
    assert [entry.weight_kg for entry in logs.weights_last_week] == [80.0]
    assert logs.case_counts == {"perfect": 1}


@pytest.mark.asyncio
async def test_load_week_logs_asks_for_the_week_between_local_midnights():
    uow = _Uow([_meal(0, 8)])

    await _load(uow)

    next_monday = datetime(2026, 10, 11, 17, tzinfo=UTC)  # 00:00 at UTC+7
    assert uow.meals.range_args == (
        USER,
        MONDAY,
        SUNDAY,
        2_000,
        TZ_NAME,
        MealProjection.MACROS_ONLY,
    )
    # Workouts back to the sign-up week, for the strength record.
    assert uow.movement_entries.args == (
        USER,
        datetime(2025, 12, 28, 17, tzinfo=UTC),
        next_monday,
    )
    # Weigh-ins from last week too, for the change since then.
    assert uow.weight_entries.args == (
        USER,
        datetime(2026, 9, 27, 17, tzinfo=UTC),
        next_monday,
    )
    assert uow.hydration_entries.args == (USER, MONDAY, SUNDAY)
    assert uow.hydration_entries.kwargs == {"user_timezone": TZ_NAME}
    assert uow.users.asked == UUID(USER)


@pytest.mark.asyncio
async def test_load_week_logs_reads_the_meal_reminder_times():
    prefs = NotificationPreferences(
        preferences_id=str(uuid4()),
        user_id=USER,
        breakfast_time_minutes=0,
        lunch_time_minutes=750,
    )
    uow = _Uow([_meal(0, 8)], prefs=prefs)

    logs = await _load(uow)

    assert logs is not None
    # Midnight is a time like any other; dinner has none, so it keeps 6 PM.
    assert logs.reminders == {"breakfast": 0, "lunch": 750, "dinner": 1080}
    assert uow.notifications.asked == USER


@pytest.mark.asyncio
async def test_load_week_logs_without_reminder_settings_uses_the_defaults():
    logs = await _load(_Uow([_meal(0, 8)]))

    assert logs is not None
    assert logs.reminders == {"breakfast": 510, "lunch": 690, "dinner": 1080}


@pytest.mark.asyncio
async def test_load_week_logs_leaves_out_logs_from_before_sign_up():
    uow = _Uow(
        [_meal(0, 8), _meal(2, 9), _meal(4, 12)],
        movements=[_move(1), _move(3)],
        drinks=[_drink(1), _drink(2), _drink(5)],
    )

    logs = await _load(uow, created_at=_utc(2, 10))  # Wednesday 10:00

    assert logs is not None
    assert logs.created == date(2026, 10, 7)
    assert [meal.day for meal in logs.meals] == [2, 4]
    assert [entry.logged_at for entry in logs.movements] == [_utc(3, 18)]
    assert [entry.logged_at for entry in logs.drinks] == [_utc(2, 15), _utc(5, 15)]
    assert uow.movement_entries.args[1] == datetime(2026, 10, 4, 17, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("sign_up", [5, 6, 9])
async def test_load_week_logs_needs_three_days_of_the_week(sign_up):
    uow = _Uow([_meal(6, 12)])

    assert await _load(uow, created_at=_utc(sign_up, 10)) is None
    assert uow.meals.range_args is None


@pytest.mark.asyncio
async def test_load_week_logs_needs_food_in_the_week():
    uow = _Uow(
        [_meal(1, 9, source="hydration"), _meal(-2, 12)],
        movements=[_move(3)],
    )

    assert await _load(uow) is None
    assert uow.movement_entries.args is None


@pytest.mark.asyncio
async def test_load_week_logs_without_a_sign_up_time_starts_at_the_first_log():
    first_log = MONDAY - timedelta(days=20)
    uow = _Uow([_meal(3, 12)], log_dates=[MONDAY + timedelta(days=3), first_log])

    logs = await _load(uow, created_at=None)

    assert logs is not None
    assert logs.created == first_log


@pytest.mark.asyncio
async def test_load_week_logs_without_a_profile_reads_as_recomp():
    logs = await _load(_Uow([_meal(0, 12)], profile=None))

    assert logs is not None
    assert (logs.goal, logs.profile) == ("recomp", None)


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (datetime(2026, 10, 4, 18, tzinfo=UTC), date(2026, 10, 5)),
        (datetime(2026, 10, 4, 16, 59), date(2026, 10, 4)),  # naive is UTC
        (None, date(2026, 9, 1)),
    ],
)
def test_sign_up_day_is_local(created_at, expected):
    assert sign_up_day(created_at, TZ, fallback=date(2026, 9, 1)) == expected


@pytest.mark.asyncio
async def test_load_week_logs_reads_the_recap_of_the_week_before():
    last = _recap(MONDAY - timedelta(days=7), "steady")
    older = _recap(MONDAY - timedelta(days=14), "perfect")
    uow = _Uow([_meal(0, 8)], recaps=[older, last])

    logs = await load_week_logs(
        uow, USER, MONDAY, TZ_NAME, LONG_AGO, 3, first_name="Linh"
    )

    assert logs is not None
    assert logs.last_recap is last
    assert logs.first_name == "Linh"


@pytest.mark.asyncio
async def test_load_week_logs_without_a_recap_last_week_has_none():
    uow = _Uow([_meal(0, 8)], recaps=[_recap(MONDAY - timedelta(days=14), "steady")])

    logs = await _load(uow)

    assert logs is not None
    assert (logs.last_recap, logs.first_name) == (None, None)
