"""
Unit tests for timezone utilities.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.domain.utils.timezone_utils import (
    DEFAULT_BREAKFAST_TIME_MINUTES,
    DEFAULT_SLEEP_TIME_MINUTES,
    DEFAULT_TIMEZONE,
    get_zone_info,
    is_in_quiet_hours,
    is_valid_timezone,
    resolve_user_timezone,
    resolve_user_timezone_async,
    utc_to_local_minutes,
)


class TestGetZoneInfo:
    """Test get_zone_info function."""

    def test_valid_timezone(self):
        """Test with valid IANA timezone."""
        zone = get_zone_info("America/Los_Angeles")
        assert isinstance(zone, ZoneInfo)
        assert zone.key == "America/Los_Angeles"

    def test_invalid_timezone_fallback(self):
        """Test invalid timezone falls back to UTC."""
        zone = get_zone_info("Invalid/Timezone")
        assert isinstance(zone, ZoneInfo)
        assert zone.key == DEFAULT_TIMEZONE

    def test_empty_timezone_fallback(self):
        """Test empty timezone falls back to UTC."""
        zone = get_zone_info("")
        assert isinstance(zone, ZoneInfo)
        assert zone.key == DEFAULT_TIMEZONE

    def test_none_timezone_fallback(self):
        """Test None timezone falls back to UTC."""
        zone = get_zone_info(None)
        assert isinstance(zone, ZoneInfo)
        assert zone.key == DEFAULT_TIMEZONE


class TestUtcToLocalMinutes:
    """Test utc_to_local_minutes function."""

    def test_utc_to_local_minutes_vietnam(self):
        """Test UTC to Vietnam timezone conversion."""
        # 2:00 UTC = 9:00 AM Vietnam (UTC+7)
        utc = datetime(2024, 12, 7, 2, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "Asia/Ho_Chi_Minh")
        assert minutes == 540  # 9:00 AM = 9 * 60 = 540 minutes

    def test_utc_to_local_minutes_us_pacific(self):
        """Test UTC to US Pacific timezone conversion."""
        # 17:00 UTC = 9:00 AM Pacific (UTC-8 in December)
        utc = datetime(2024, 12, 7, 17, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "America/Los_Angeles")
        assert minutes == 540  # 9:00 AM = 9 * 60 = 540 minutes

    def test_utc_to_local_minutes_us_eastern(self):
        """Test UTC to US Eastern timezone conversion."""
        # 14:00 UTC = 9:00 AM Eastern (UTC-5 in December)
        utc = datetime(2024, 12, 7, 14, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "America/New_York")
        assert minutes == 540  # 9:00 AM = 9 * 60 = 540 minutes

    def test_utc_to_local_minutes_midnight(self):
        """Test midnight conversion."""
        utc = datetime(2024, 12, 7, 0, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "UTC")
        assert minutes == 0

    def test_utc_to_local_minutes_invalid_timezone(self):
        """Test invalid timezone falls back to UTC."""
        utc = datetime(2024, 12, 7, 12, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "Invalid/Timezone")
        # Should fallback to UTC, so 12:00 UTC = 12:00 UTC = 720 minutes
        assert minutes == 720

    def test_dst_handling(self):
        """Test DST transition handling."""
        # March 10, 2024 - US DST starts (spring forward)
        # 9:00 UTC = 5:00 AM EDT (not 4:00 AM EST)
        utc = datetime(2024, 3, 10, 9, 0, tzinfo=ZoneInfo("UTC"))
        minutes = utc_to_local_minutes(utc, "America/New_York")
        assert minutes == 5 * 60  # 5:00 AM


class TestIsValidTimezone:
    """Test is_valid_timezone function."""

    def test_valid_timezone(self):
        """Test valid IANA timezone."""
        assert is_valid_timezone("America/Los_Angeles") is True
        assert is_valid_timezone("Asia/Ho_Chi_Minh") is True
        assert is_valid_timezone("Europe/London") is True
        assert is_valid_timezone("UTC") is True

    def test_invalid_timezone(self):
        """Test invalid timezone."""
        assert is_valid_timezone("Invalid/Timezone") is False
        assert is_valid_timezone("NotATimezone") is False

    def test_empty_timezone(self):
        """Test empty timezone."""
        assert is_valid_timezone("") is False
        assert is_valid_timezone(None) is False


class TestIsInQuietHours:
    """Tests for is_in_quiet_hours function."""

    def test_midnight_crossing_in_quiet_late_night(self):
        """User at 23:00 with sleep=22:00, wake=08:00 → in quiet"""
        assert is_in_quiet_hours(1380, 1320, 480) is True  # 23:00

    def test_midnight_crossing_in_quiet_early_morning(self):
        """User at 03:00 with sleep=22:00, wake=08:00 → in quiet"""
        assert is_in_quiet_hours(180, 1320, 480) is True  # 03:00

    def test_midnight_crossing_not_in_quiet(self):
        """User at 12:00 with sleep=22:00, wake=08:00 → not in quiet"""
        assert is_in_quiet_hours(720, 1320, 480) is False  # 12:00

    def test_at_quiet_start_boundary(self):
        """User at exactly sleep time → in quiet"""
        assert is_in_quiet_hours(1320, 1320, 480) is True  # 22:00

    def test_at_quiet_end_boundary(self):
        """User at exactly wake time → not in quiet"""
        assert is_in_quiet_hours(480, 1320, 480) is False  # 08:00

    def test_just_before_quiet_start(self):
        """User at 21:59 (one minute before sleep) → not in quiet"""
        assert is_in_quiet_hours(1319, 1320, 480) is False

    def test_just_before_quiet_end(self):
        """User at 07:59 (one minute before wake) → in quiet"""
        assert is_in_quiet_hours(479, 1320, 480) is True

    def test_none_values_use_defaults(self):
        """None values should use defaults (22:00-08:00)"""
        # 23:00 in default quiet hours
        assert is_in_quiet_hours(1380, None, None) is True
        # 12:00 not in quiet hours
        assert is_in_quiet_hours(720, None, None) is False
        # Verify defaults are correct
        assert DEFAULT_SLEEP_TIME_MINUTES == 1320
        assert DEFAULT_BREAKFAST_TIME_MINUTES == 510

    def test_partial_none_quiet_start(self):
        """None quiet_start uses default sleep time"""
        # User at 23:00, default sleep=22:00, breakfast=06:00
        assert is_in_quiet_hours(1380, None, 360) is True  # 23:00 in quiet

    def test_partial_none_quiet_end(self):
        """None quiet_end uses default breakfast time"""
        # User at 07:00, sleep=21:00, default breakfast=08:00
        assert is_in_quiet_hours(420, 1260, None) is True  # 07:00 in quiet

    def test_same_day_quiet_hours(self):
        """Same day quiet hours (no midnight crossing)"""
        # Quiet from 01:00 (60) to 05:00 (300) - unlikely but valid
        assert is_in_quiet_hours(120, 60, 300) is True  # 02:00 in quiet
        assert is_in_quiet_hours(360, 60, 300) is False  # 06:00 not in quiet
        assert is_in_quiet_hours(60, 60, 300) is True  # At start, in quiet
        assert is_in_quiet_hours(300, 60, 300) is False  # At end, not in quiet

    def test_midnight_exactly(self):
        """User at midnight (0 minutes) with sleep=22:00, wake=08:00 → in quiet"""
        assert is_in_quiet_hours(0, 1320, 480) is True

    def test_afternoon_not_in_quiet(self):
        """User at 15:00 (900 minutes) → not in quiet"""
        assert is_in_quiet_hours(900, 1320, 480) is False


from unittest.mock import AsyncMock, MagicMock

import pytest


class TestResolveUserTimezoneAsync:
    """Test resolve_user_timezone_async precedence (DB -> Header -> UTC)."""

    @pytest.mark.asyncio
    async def test_prefers_stored_db_timezone_over_header(self):
        uow = MagicMock()
        mock_user = MagicMock()
        mock_user.timezone = "America/New_York"
        uow.users.find_by_id = AsyncMock(return_value=mock_user)

        tz = await resolve_user_timezone_async(
            user_id="user-123",
            uow=uow,
            header_timezone="Asia/Tokyo",
        )
        assert tz == "America/New_York"

    @pytest.mark.asyncio
    async def test_uses_header_when_db_is_utc(self):
        uow = MagicMock()
        mock_user = MagicMock()
        mock_user.timezone = "UTC"
        uow.users.find_by_id = AsyncMock(return_value=mock_user)

        tz = await resolve_user_timezone_async(
            user_id="user-123",
            uow=uow,
            header_timezone="Asia/Tokyo",
        )
        assert tz == "Asia/Tokyo"

    @pytest.mark.asyncio
    async def test_uses_header_when_user_not_found(self):
        uow = MagicMock()
        uow.users.find_by_id = AsyncMock(return_value=None)

        tz = await resolve_user_timezone_async(
            user_id="user-123",
            uow=uow,
            header_timezone="Europe/London",
        )
        assert tz == "Europe/London"

    @pytest.mark.asyncio
    async def test_uses_get_user_timezone_fast_path_when_available(self):
        uow = MagicMock()
        uow.users.get_user_timezone = AsyncMock(return_value="Asia/Bangkok")
        uow.users.find_by_id = AsyncMock()

        tz = await resolve_user_timezone_async(
            user_id="user-123",
            uow=uow,
            header_timezone="Asia/Tokyo",
        )
        assert tz == "Asia/Bangkok"
        uow.users.get_user_timezone.assert_awaited_once_with("user-123")
        uow.users.find_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_get_user_timezone_utc_allows_header(self):
        uow = MagicMock()
        uow.users.get_user_timezone = AsyncMock(return_value="UTC")
        uow.users.find_by_id = AsyncMock()

        tz = await resolve_user_timezone_async(
            user_id="user-123",
            uow=uow,
            header_timezone="Asia/Tokyo",
        )
        assert tz == "Asia/Tokyo"
        uow.users.get_user_timezone.assert_awaited_once_with("user-123")
        uow.users.find_by_id.assert_not_called()


class TestResolveUserTimezone:
    """Test synchronous resolve_user_timezone precedence (DB -> Header -> UTC)."""

    def test_prefers_stored_db_timezone_over_header(self):
        uow = MagicMock()
        mock_user = MagicMock()
        mock_user.timezone = "America/New_York"
        uow.users.find_by_id = MagicMock(return_value=mock_user)

        tz = resolve_user_timezone(
            user_id="user-123",
            uow=uow,
            header_timezone="Asia/Tokyo",
        )
        assert tz == "America/New_York"
