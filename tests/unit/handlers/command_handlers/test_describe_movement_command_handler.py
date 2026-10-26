"""Ghi nhanh activity: one sentence saves every activity, priced like the app."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.exceptions import BusinessLogicException, ValidationException
from src.app.commands.movement import DescribeMovementCommand
from src.app.events.movement.local_cache_invalidation import (
    register_local_movement_cache_invalidation_hook,
)
from src.app.handlers.command_handlers.describe_movement_command_handler import (
    DescribeMovementCommandHandler,
)
from src.domain.services.movement_catalog_service import active_kcal, get_met
from src.domain.utils.timezone_utils import utc_now

USER_ID = "user-1"


class _Store:
    def __init__(self, profile_weight=None) -> None:
        self.entries: list = []
        self.uows: list[_Uow] = []
        self.profile_weight = profile_weight


class _Entries:
    def __init__(self, uow: "_Uow") -> None:
        self.uow = uow

    async def add(self, entry):
        self.uow.added.append(entry)
        self.uow.store.entries.append(entry)
        return entry


class _Users:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def get_user_timezone(self, user_id):
        return "UTC"

    async def get_profile(self, user_id):
        if self.store.profile_weight is None:
            return None
        return SimpleNamespace(weight_kg=self.store.profile_weight)


class _Uow:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.users = _Users(store)
        self.movement_entries = _Entries(self)
        self.added: list = []
        self.committed = False
        store.uows.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.committed = exc_type is None
        return False


def _item(catalog_id=None, name="", minutes=None, intensity=None, met=None, km=None):
    return {
        "catalog_id": catalog_id,
        "name": name,
        "duration_min": minutes,
        "intensity": intensity,
        "distance_km": km,
        "met": met,
    }


@pytest.fixture
def cache_hook():
    hook = AsyncMock()
    register_local_movement_cache_invalidation_hook(hook)
    yield hook
    register_local_movement_cache_invalidation_hook(None)


def _handler(store, *replies, publisher=None):
    generation = AsyncMock()
    generation.generate_meal_plan_async = AsyncMock(side_effect=list(replies))
    handler = DescribeMovementCommandHandler(
        uow_factory=lambda: _Uow(store),
        meal_generation_service=generation,
        event_publisher=publisher or AsyncMock(),
    )
    return handler, generation


def _command(text="chạy 30 phút", **kwargs):
    return DescribeMovementCommand(user_id=USER_ID, text=text, **kwargs)


@pytest.mark.asyncio
async def test_listed_activities_get_catalog_names_and_app_kcal(cache_hook):
    store = _Store()
    publisher = AsyncMock()
    handler, _ = _handler(
        store,
        {
            "items": [
                _item("running", "chạy", minutes=30, km=5),
                _item("gym_strength", "tập tạ", minutes=45),
            ]
        },
        publisher=publisher,
    )

    result = await handler.handle(
        _command("chạy 5km 30 phút rồi tập tạ 45 phút", weight_kg=62)
    )

    running, gym = result["entries"]
    assert (running["activity_id"], running["activity_name"]) == ("running", "Running")
    assert (gym["activity_id"], gym["activity_name"]) == (
        "gym_strength",
        "Gym / Strength",
    )
    assert running["intensity"] == gym["intensity"] == "moderate"
    assert running["kcal_burned"] == active_kcal(8.5, 62, 30)
    assert gym["kcal_burned"] == active_kcal(5.0, 62, 45)
    assert running["include_in_balance"] is True
    write_uow = store.uows[-1]
    assert len(write_uow.added) == 2 and write_uow.committed
    assert publisher.publish.await_count == 2
    assert {c.args[0]["event_type"] for c in publisher.publish.await_args_list} == {
        "movement.created.v1"
    }
    cache_hook.assert_awaited_once_with(USER_ID, utc_now().date())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "minutes"),
    [
        (_item("swimming", "bơi"), 45),
        (_item("cardio_hiit", "hiit"), 20),
        (_item(None, "nhảy zumba", met=7.3), 45),
        (_item("running", "chạy", minutes=900), 600),
        (_item("running", "chạy", minutes=float("nan")), 30),
        (_item("running", "chạy", minutes=0.4), 1),
    ],
)
async def test_minutes_default_and_clamp(cache_hook, item, minutes):
    store = _Store()
    handler, _ = _handler(store, {"items": [item]})

    await handler.handle(_command(weight_kg=62))

    assert store.entries[0].duration_min == minutes


@pytest.mark.asyncio
async def test_unlisted_activity_keeps_the_persons_words(cache_hook):
    store = _Store()
    handler, _ = _handler(
        store,
        {
            "items": [
                _item(None, "leo núi", minutes=120, met=7.0),
                _item(None, "đi dạo", minutes=40, met=1.3),
            ]
        },
    )

    result = await handler.handle(
        _command("leo núi 2 tiếng, đi dạo 40 phút", weight_kg=70)
    )

    hike, stroll = result["entries"]
    assert hike["activity_id"] is None
    assert (hike["activity_name"], hike["duration_min"]) == ("leo núi", 120)
    assert hike["kcal_burned"] == active_kcal(7.0, 70, 120)
    assert stroll["kcal_burned"] == active_kcal(1.5, 70, 40)


@pytest.mark.asyncio
async def test_intensity_picks_the_catalog_met_and_falls_back_to_moderate(cache_hook):
    store = _Store()
    handler, _ = _handler(
        store,
        {
            "items": [
                _item("swimming", "bơi nhanh", minutes=30, intensity="HARD"),
                _item("swimming", "bơi", minutes=30, intensity="extreme"),
            ]
        },
    )

    result = await handler.handle(_command(weight_kg=62))

    fast, unknown = result["entries"]
    assert fast["intensity"] == "hard"
    assert fast["kcal_burned"] == active_kcal(get_met("swimming", "hard"), 62, 30)
    assert unknown["intensity"] == "moderate"


@pytest.mark.asyncio
async def test_weight_comes_from_the_request_then_profile_then_62(cache_hook):
    reply = {"items": [_item("running", "chạy", minutes=30)]}
    with_profile = _Store(profile_weight=80.0)
    no_profile = _Store()

    await _handler(with_profile, reply)[0].handle(_command())
    await _handler(no_profile, reply)[0].handle(_command())
    sent = _Store(profile_weight=80.0)
    await _handler(sent, reply)[0].handle(_command(weight_kg=55))

    assert with_profile.entries[0].kcal_burned == active_kcal(8.5, 80, 30)
    assert no_profile.entries[0].kcal_burned == active_kcal(8.5, 62, 30)
    assert sent.entries[0].kcal_burned == active_kcal(8.5, 55, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "profile", "priced_at"),
    [
        (20, 80.0, 30.0),  # out-of-range request weight is clamped, not refused
        (350, None, 300.0),
        (0, 80.0, 80.0),  # unusable request weight falls back to the profile
        (None, 25.0, 30.0),  # profile weight is clamped the same way
        (float("nan"), None, 62.0),
    ],
)
async def test_weight_is_clamped_not_rejected(cache_hook, sent, profile, priced_at):
    store = _Store(profile_weight=profile)
    handler, _ = _handler(store, {"items": [_item("running", "chạy", minutes=30)]})

    await handler.handle(_command(weight_kg=sent))

    # 30 kcal a minute is the /movement/log ceiling.
    assert store.entries[0].kcal_burned == min(active_kcal(8.5, priced_at, 30), 900)


@pytest.mark.asyncio
async def test_kcal_is_clamped_to_the_log_route_bounds(cache_hook):
    store = _Store()
    handler, _ = _handler(
        store, {"items": [_item(None, "chạy siêu tốc", minutes=600, met=40)]}
    )

    await handler.handle(_command(weight_kg=300))

    entry = store.entries[0]
    assert entry.kcal_burned == 5000.0
    assert entry.kcal_burned <= entry.duration_min * 30


@pytest.mark.asyncio
async def test_bad_items_are_dropped_and_overflow_is_cut(cache_hook):
    store = _Store()
    items = [
        _item(None, "leo núi"),  # unlisted without a MET
        _item(None, "", met=5),
        *[_item("walking", f"đi bộ {n}") for n in range(20)],
    ]
    handler, _ = _handler(store, {"items": items})

    result = await handler.handle(_command(weight_kg=62))

    assert len(result["entries"]) == 8
    assert {entry["activity_id"] for entry in result["entries"]} == {"walking"}


@pytest.mark.asyncio
async def test_nothing_recognised_saves_nothing(cache_hook):
    store = _Store()
    handler, _ = _handler(store, {"items": [_item(None, "leo núi")]})

    with pytest.raises(BusinessLogicException) as exc_info:
        await handler.handle(_command("leo núi"))

    assert exc_info.value.error_code == "DESCRIBE_NOTHING_RECOGNIZED"
    assert store.entries == [] and len(store.uows) == 1
    cache_hook.assert_not_called()


@pytest.mark.asyncio
async def test_publish_failure_still_returns_the_saved_entries(cache_hook):
    store = _Store()
    publisher = AsyncMock()
    publisher.publish.side_effect = ConnectionError("queue down")
    handler, _ = _handler(
        store, {"items": [_item("running", "chạy", minutes=30)]}, publisher=publisher
    )

    result = await handler.handle(
        _command(weight_kg=62, target_date=date(2026, 10, 24))
    )

    assert len(result["entries"]) == 1
    cache_hook.assert_awaited_once_with(USER_ID, date(2026, 10, 24))


@pytest.mark.asyncio
async def test_two_days_after_today_is_refused_before_the_model(cache_hook):
    store = _Store()
    handler, generation = _handler(store, {"items": [_item("running", "chạy")]})

    with pytest.raises(ValidationException) as exc_info:
        await handler.handle(_command(target_date=utc_now().date() + timedelta(days=2)))

    assert exc_info.value.error_code == "INVALID_DATE"
    generation.generate_meal_plan_async.assert_not_called()
