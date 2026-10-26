"""Unlisted Ghi nhanh drinks (drink_id "other") keep the person's words in every feed."""

from datetime import UTC, datetime

import pytest

from src.app.handlers.query_handlers import get_bulk_activities_query_handler as bulk
from src.app.handlers.query_handlers.get_daily_activities_query_handler import (
    GetDailyActivitiesQueryHandler,
)
from src.domain.model.hydration.hydration_entry import HydrationEntry


def _entry(name: str, drink_id: str) -> HydrationEntry:
    return HydrationEntry(
        user_id="user-1",
        drink_name_snapshot=name,
        drink_id=drink_id,
        volume_ml=250,
        credited_ml=225,
        logged_at=datetime(2026, 10, 26, 3, tzinfo=UTC),
        carbs_g=45.0,
        sugar_g=42.5,
    )


@pytest.mark.parametrize("language", ["vi", "en", "ja"])
def test_day_and_bulk_feeds_title_unlisted_drinks_with_the_persons_words(language):
    entry = _entry("nước mía", "other")

    day = GetDailyActivitiesQueryHandler()._build_hydration_entry_activity(
        entry, language
    )
    feed = bulk._build_hydration_entry_activity(entry, language)

    assert day["title"] == feed["title"] == "nước mía"
    assert day["drink_id"] == feed["drink_id"] == "other"


def test_listed_drinks_keep_their_localised_catalog_title():
    entry = _entry("Milk tea", "milk-tea")

    assert bulk._build_hydration_entry_activity(entry, "vi")["title"] == "Trà sữa"
