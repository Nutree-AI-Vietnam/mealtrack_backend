"""Tests for new CacheKeys static methods."""

from datetime import date

from src.domain.cache.cache_keys import CacheKeys


class TestCacheKeysNewMethods:

    def test_user_streak_key_format(self):
        key, ttl = CacheKeys.user_streak("user-123")
        assert key == "user:streak:user-123"
        assert ttl == CacheKeys.TTL_1_HOUR

    def test_daily_activities_key_format(self):
        key, ttl = CacheKeys.daily_activities("user-123", date(2026, 4, 14))
        assert key == "user:user-123:activities:2026-04-14:en"  # Default language
        assert ttl == 300  # 5 minutes

    def test_daily_activities_key_with_language(self):
        key_en, _ = CacheKeys.daily_activities("user-123", date(2026, 4, 14), "en")
        key_vi, _ = CacheKeys.daily_activities("user-123", date(2026, 4, 14), "vi")
        assert key_en == "user:user-123:activities:2026-04-14:en"
        assert key_vi == "user:user-123:activities:2026-04-14:vi"
        assert key_en != key_vi  # Different languages produce different keys

    def test_saved_suggestions_key_format(self):
        key, ttl = CacheKeys.saved_suggestions("user-123")
        assert key == "user:user-123:saved_suggestions"
        assert ttl == CacheKeys.TTL_1_HOUR


    def test_keys_are_distinct_per_user(self):
        key_a, _ = CacheKeys.user_streak("user-a")
        key_b, _ = CacheKeys.user_streak("user-b")
        assert key_a != key_b

    def test_activities_keys_are_distinct_per_date(self):
        key_a, _ = CacheKeys.daily_activities("user-1", date(2026, 4, 14))
        key_b, _ = CacheKeys.daily_activities("user-1", date(2026, 4, 15))
        assert key_a != key_b

    def test_activities_keys_are_distinct_per_language(self):
        key_a, _ = CacheKeys.daily_activities("user-1", date(2026, 4, 14), "en")
        key_b, _ = CacheKeys.daily_activities("user-1", date(2026, 4, 14), "vi")
        assert key_a != key_b
