"""GET /v1/recaps/latest: the newest week's recap, built once and then kept."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.fixtures.fakes.fake_weekly_recap_repository import (
    FakeWeeklyRecapRepository,
)
from tests.fixtures.weekly_recap_samples import sample_stats

from src.api.exceptions import ResourceNotFoundException
from src.app.handlers.query_handlers.get_latest_weekly_recap_query_handler import (
    GetLatestWeeklyRecapQueryHandler,
)
from src.app.queries.progress import GetLatestWeeklyRecapQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Macros, Nutrition
from src.domain.model.weekly_recap import (
    WeeklyRecap,
    week_stats_from_dict,
    week_stats_to_dict,
)
from src.domain.services.weekly_recap import RecapConfig, generate_recap

TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)
MONDAY = date(2026, 10, 5)
SUNDAY = date(2026, 10, 11)
USER = "11111111-1111-1111-1111-111111111111"
CONFIG = RecapConfig()
NOW = datetime(2026, 10, 12, 2, tzinfo=UTC)  # Monday 09:00 at UTC+7
KCAL = (1800.0, 1900.0, 2100.0, 1700.0, 2400.0, 2600.0, 1500.0)
ACCOUNT = SimpleNamespace(timezone=TZ_NAME, created_at=datetime(2026, 1, 1, tzinfo=UTC))
PROFILE = SimpleNamespace(
    fitness_goal="cut",
    weight_kg=80.0,
    target_weight_kg=70.0,
    goal_start_weight_kg=None,
)


def _meal(day: int, hour: int) -> Meal:
    local = datetime.combine(MONDAY + timedelta(days=day), time(hour), TZ)
    created = local.astimezone(UTC)
    return Meal(
        meal_id=str(uuid4()),
        user_id=USER,
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Phở bò",
        nutrition=Nutrition(macros=Macros(protein=30.0, carbs=60.0, fat=15.0)),
        ready_at=created,
        source="scanner",
    )


def _row(day: int, calories: float, target: int = 2000) -> dict:
    return {
        "date": (MONDAY + timedelta(days=day)).isoformat(),
        "calories": calories,
        "protein_g": 110.0,
        "fiber_g": 20.0,
        "fiber_target_g": 28.0,
        "target_calories": target,
        "hydration_ml": 2000,
        "hydration_goal_ml": 2500,
        "protein_target_g": 120.0,
        "is_cheat_day": False,
    }


WEEK_MEALS = tuple(_meal(day, hour) for day in range(7) for hour in (8, 12, 19))
WEEK_ROWS = tuple(_row(day, kcal) for day, kcal in enumerate(KCAL))


def _stored(week_start: date = MONDAY, **values) -> WeeklyRecap:
    """A recap stored in Vietnamese for the steady sample week."""
    stats = sample_stats("steady")
    recap = {
        "user_id": USER,
        "week_start": week_start,
        "case_id": "steady",
        "locale": "vi",
        "stats": week_stats_to_dict(stats),
        "payload": generate_recap(stats, CONFIG, "vi"),
    }
    return WeeklyRecap(**(recap | values))


class _Users:
    def __init__(self, account):
        self._account = account

    async def find_by_id(self, user_id):
        return self._account

    async def get_profile(self, user_id):
        return PROFILE


class _Meals:
    def __init__(self, meals):
        self._meals = list(meals)
        self.read = False

    async def get_dates_with_meals(self, user_id, user_timezone=None):
        days = {meal.created_at.astimezone(TZ).date() for meal in self._meals}
        return sorted(days, reverse=True)

    async def find_by_date_range(self, user_id, start_date, end_date, **kwargs):
        self.read = True
        return self._meals


class _NoEntries:
    async def _none(self, *args, **kwargs):
        return []

    find_by_user_and_logged_range = _none
    find_by_recorded_range = _none
    find_by_date_range = _none


class _NoPreferences:
    async def find_notification_preferences_by_user(self, user_id):
        return None


class _Uow:
    def __init__(self, *, meals=WEEK_MEALS, account=ACCOUNT, recaps=()):
        self.users = _Users(account)
        self.meals = _Meals(meals)
        self.movement_entries = _NoEntries()
        self.weight_entries = _NoEntries()
        self.hydration_entries = _NoEntries()
        self.weekly_recaps = FakeWeeklyRecapRepository(recaps)
        self.notifications = _NoPreferences()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _Summary:
    """Stands in for the progress summary: hands back the week's day rows."""

    def __init__(self, rows=WEEK_ROWS):
        self._rows = rows
        self.queries = []

    async def handle(self, query):
        self.queries.append(query)
        return {"days": [dict(row) for row in self._rows]}


def _handler(uow: _Uow, summary: _Summary | None = None, now: datetime = NOW):
    return GetLatestWeeklyRecapQueryHandler(
        uow_factory=lambda: uow,
        summary_handler=summary or _Summary(),
        config=CONFIG,
        now_fn=lambda: now,
    )


async def _read(handler: GetLatestWeeklyRecapQueryHandler, locale: str = "vi"):
    query = GetLatestWeeklyRecapQuery(
        user_id=USER, header_timezone="Europe/Paris", locale=locale
    )
    return await handler.handle(query)


@pytest.mark.asyncio
async def test_first_read_builds_the_recap_and_keeps_it():
    uow, summary = _Uow(), _Summary()

    payload = await _read(_handler(uow, summary))

    assert payload is not None
    assert payload["week_start"] == "2026-10-05"
    [query] = summary.queries
    assert (query.user_id, query.start_date, query.end_date) == (USER, MONDAY, SUNDAY)
    assert query.header_timezone == "Europe/Paris"
    [stored] = uow.weekly_recaps.inserted
    assert (stored.user_id, stored.week_start, stored.locale) == (USER, MONDAY, "vi")
    assert (stored.case_id, stored.payload) == (payload["case_id"], payload)
    assert stored.stats["kcal_by_day"] == list(KCAL)
    # The stored numbers tell the same story again.
    assert generate_recap(week_stats_from_dict(stored.stats), CONFIG, "vi") == payload


@pytest.mark.asyncio
async def test_later_reads_return_the_stored_recap():
    stored = _stored()
    uow, summary = _Uow(recaps=[stored]), _Summary()

    assert await _read(_handler(uow, summary)) == stored.payload
    assert summary.queries == []
    assert uow.meals.read is False
    assert uow.weekly_recaps.inserted == []


@pytest.mark.asyncio
async def test_another_language_retells_the_stored_numbers():
    stored = _stored()
    uow = _Uow(recaps=[stored])

    payload = await _read(_handler(uow), locale="en")

    assert payload == generate_recap(sample_stats("steady"), CONFIG, "en")
    assert payload != stored.payload
    assert uow.weekly_recaps.recaps[(USER, MONDAY)] is stored
    assert uow.meals.read is False


@pytest.mark.asyncio
async def test_another_language_keeps_the_stored_story():
    # Picked as "perfect" under the config of the time; today's config reads
    # the same numbers as "steady", so the stored words stand.
    stored = _stored(case_id="perfect", payload={"case_id": "perfect"})

    payload = await _read(_handler(_Uow(recaps=[stored])), locale="en")

    assert payload == {"case_id": "perfect"}


@pytest.mark.asyncio
async def test_an_unreadable_snapshot_keeps_the_stored_words():
    stored = _stored(stats={"week_start": "2026-10-05"})

    payload = await _read(_handler(_Uow(recaps=[stored])), locale="en")

    assert payload == stored.payload


@pytest.mark.parametrize(
    ("now", "week_start"),
    [
        (datetime(2026, 10, 12, 1, tzinfo=UTC), MONDAY),  # Monday 08:00 local
        (datetime(2026, 10, 12, 0, 59, tzinfo=UTC), MONDAY - timedelta(days=7)),
        (datetime(2026, 10, 18, 16, 59, tzinfo=UTC), MONDAY),  # Sunday 23:59
    ],
)
@pytest.mark.asyncio
async def test_a_week_comes_out_the_next_monday_at_eight_local_time(now, week_start):
    recaps = [_stored(MONDAY - timedelta(days=7), payload={"week": "earlier"})]
    recaps.append(_stored(MONDAY, payload={"week": "latest"}))
    uow = _Uow(recaps=recaps)

    payload = await _read(_handler(uow, now=now))

    assert payload == uow.weekly_recaps.recaps[(USER, week_start)].payload


@pytest.mark.asyncio
async def test_a_week_with_nothing_to_recap_gives_none():
    uow, summary = _Uow(meals=()), _Summary()

    assert await _read(_handler(uow, summary)) is None
    assert summary.queries == []
    assert uow.weekly_recaps.inserted == []


@pytest.mark.asyncio
async def test_a_week_without_a_calorie_target_gives_none():
    uow = _Uow()
    summary = _Summary(rows=[_row(day, 1800.0, target=0) for day in range(7)])

    assert await _read(_handler(uow, summary)) is None
    assert uow.weekly_recaps.inserted == []


@pytest.mark.asyncio
async def test_an_unknown_user_is_not_found():
    with pytest.raises(ResourceNotFoundException):
        await _read(_handler(_Uow(account=None)))


class _RacingRecaps(FakeWeeklyRecapRepository):
    """Another request stores the week between this one's read and write."""

    def __init__(self, first: WeeklyRecap):
        super().__init__()
        self._first = first

    async def insert_if_absent(self, recap: WeeklyRecap) -> bool:
        self.recaps[(self._first.user_id, self._first.week_start)] = self._first
        return await super().insert_if_absent(recap)


@pytest.mark.asyncio
async def test_a_lost_race_returns_the_recap_stored_first():
    first = _stored(payload={"case_id": "steady", "stored": "first"})
    uow = _Uow()
    uow.weekly_recaps = _RacingRecaps(first)

    payload = await _read(_handler(uow))

    assert payload == {"case_id": "steady", "stored": "first"}
    assert uow.weekly_recaps.inserted == []


@pytest.mark.parametrize(
    ("header", "locale"),
    [("vi-VN,vi;q=0.9,en;q=0.8", "vi"), ("ko-KR", "en"), ("", "en"), ("ja", "ja")],
)
def test_the_query_reads_the_app_language(header, locale):
    assert GetLatestWeeklyRecapQuery(user_id=USER, locale=header).locale == locale
