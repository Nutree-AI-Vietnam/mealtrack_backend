"""Timezone utilities for notification scheduling and datetime operations."""

import logging
from datetime import UTC, date, datetime, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort

logger = logging.getLogger(__name__)

timezone = datetime_timezone

# Legacy IANA timezone aliases that may not resolve on slim Docker images
_TIMEZONE_ALIASES: dict[str, str] = {
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "Europe/Kiev": "Europe/Kyiv",
    "Pacific/Samoa": "Pacific/Pago_Pago",
}


def normalize_timezone(timezone_str: str) -> str:
    """Normalize legacy timezone aliases to canonical IANA names."""
    return _TIMEZONE_ALIASES.get(timezone_str, timezone_str)


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime.

    Use this instead of datetime.now() or datetime.utcnow().
    """
    return datetime.now(UTC)


def format_iso_utc(dt: datetime | None) -> str | None:
    """Format datetime as ISO 8601 with UTC timezone suffix.

    Args:
        dt: Datetime to format (can be naive or aware)

    Returns:
        ISO string with timezone, or None if dt is None
    """
    if dt is None:
        return None
    # If naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware UTC.

    Converts naive datetimes by assuming they are UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# Default timezone
DEFAULT_TIMEZONE = "UTC"

# Default quiet hours (for water reminders)
DEFAULT_SLEEP_TIME_MINUTES = 1320  # 10:00 PM
DEFAULT_BREAKFAST_TIME_MINUTES = 510  # 8:30 AM


def get_zone_info(timezone_str: str) -> ZoneInfo:
    """
    Get ZoneInfo for timezone string with fallback to UTC.

    Args:
        timezone_str: IANA timezone identifier (e.g., "America/Los_Angeles")

    Returns:
        ZoneInfo object, defaults to UTC if timezone invalid
    """
    if not timezone_str:
        return ZoneInfo(DEFAULT_TIMEZONE)

    try:
        return ZoneInfo(normalize_timezone(timezone_str))
    except ZoneInfoNotFoundError:
        logger.warning(f"Invalid timezone '{timezone_str}', falling back to UTC")
        return ZoneInfo(DEFAULT_TIMEZONE)


def utc_to_local_minutes(utc_time: datetime, timezone_str: str) -> int:
    """
    Convert UTC datetime to local time minutes from midnight.

    Args:
        utc_time: UTC datetime (must be timezone-aware)
        timezone_str: IANA timezone identifier

    Returns:
        Minutes from midnight in user's local time (0-1439)
    """
    zone_info = get_zone_info(timezone_str)
    local_time = utc_time.astimezone(zone_info)
    return local_time.hour * 60 + local_time.minute


def is_valid_timezone(timezone_str: str) -> bool:
    """
    Check if timezone string is valid IANA identifier (including legacy aliases).

    Args:
        timezone_str: Timezone string to validate

    Returns:
        True if valid IANA timezone
    """
    if not timezone_str:
        return False

    try:
        ZoneInfo(normalize_timezone(timezone_str))
        return True
    except ZoneInfoNotFoundError:
        return False


def is_in_quiet_hours(
    local_minutes: int, quiet_start: int | None, quiet_end: int | None
) -> bool:
    """
    Check if local_minutes falls within quiet hours window.

    Handles midnight crossing (quiet_start > quiet_end).
    Uses defaults if values are None.

    Args:
        local_minutes: Current local time in minutes (0-1439)
        quiet_start: Quiet hours start (sleep time) in minutes, or None
        quiet_end: Quiet hours end (breakfast time) in minutes, or None

    Returns:
        True if in quiet hours, False otherwise
    """
    start = quiet_start if quiet_start is not None else DEFAULT_SLEEP_TIME_MINUTES
    end = quiet_end if quiet_end is not None else DEFAULT_BREAKFAST_TIME_MINUTES

    if start > end:
        # Crosses midnight: e.g., 22:00 to 08:00
        return local_minutes >= start or local_minutes < end
    else:
        # Same day: e.g., 01:00 to 05:00
        return start <= local_minutes < end


def get_user_monday(
    target_date: date | datetime,
    user_id: str,
    uow: Optional["AsyncUnitOfWorkPort"] = None,
) -> date:
    """
    Get the Monday of the week for a given date in user's timezone.

    Args:
        target_date: The date to get the Monday for
        user_id: User's Firebase UID
        uow: Optional UnitOfWork to fetch user's timezone

    Returns:
        Monday of that week (date object)
    """
    # Get user timezone, default to UTC
    user_timezone = "UTC"

    if uow:
        try:
            user = uow.users.find_by_id(user_id)
            if user and user.timezone:
                user_timezone = user.timezone
        except Exception:
            pass

    tz = get_zone_info(user_timezone)

    # Get the local date in user's timezone
    if isinstance(target_date, datetime):
        # datetime: convert to user's timezone then extract date
        local_date = target_date.astimezone(tz).date()
    else:
        # plain date: already represents a local date, use directly
        local_date = target_date

    # Get Monday (weekday() returns 0=Monday, 6=Sunday)
    monday_offset = local_date.weekday()
    monday = local_date - timedelta(days=monday_offset)

    return monday


async def get_user_monday_async(
    target_date: date | datetime,
    user_id: str,
    uow,  # AsyncUnitOfWorkPort
) -> date:
    """Async version of get_user_monday for AsyncUnitOfWork."""
    user_timezone = "UTC"

    if uow:
        try:
            user = await uow.users.find_by_id(user_id)
            if user and user.timezone:
                user_timezone = user.timezone
        except Exception:
            pass

    tz = get_zone_info(user_timezone)

    if isinstance(target_date, datetime):
        local_date = target_date.astimezone(tz).date()
    else:
        local_date = target_date

    monday_offset = local_date.weekday()
    monday = local_date - timedelta(days=monday_offset)

    return monday


async def resolve_user_timezone_async(
    user_id: str,
    uow,  # AsyncUnitOfWorkPort
    header_timezone: str | None = None,
) -> str:
    """Async version of resolve_user_timezone for use with AsyncUnitOfWork."""
    db_tz = "UTC"
    try:
        user = await uow.users.find_by_id(user_id)
        if user and user.timezone and user.timezone != "UTC":
            return user.timezone
        if user and user.timezone:
            db_tz = user.timezone
    except Exception:
        pass

    if (
        header_timezone
        and header_timezone != "UTC"
        and is_valid_timezone(header_timezone)
    ):
        return normalize_timezone(header_timezone)

    return db_tz


def resolve_user_timezone(
    user_id: str,
    uow: "AsyncUnitOfWorkPort",
    header_timezone: str | None = None,
) -> str:
    """Resolve user timezone: DB → X-Timezone header → UTC.

    Also opportunistically updates DB if header provides
    a real timezone but DB still has 'UTC'.
    """
    # 1. Try DB
    db_tz = "UTC"
    try:
        user = uow.users.find_by_id(user_id)
        if user and user.timezone and user.timezone != "UTC":
            return user.timezone
        if user and user.timezone:
            db_tz = user.timezone
    except Exception:
        pass

    # 2. Try header fallback
    if (
        header_timezone
        and header_timezone != "UTC"
        and is_valid_timezone(header_timezone)
    ):
        canonical_tz = normalize_timezone(header_timezone)
        # Opportunistic DB update — separate try to avoid breaking caller's UoW
        if db_tz == "UTC":
            try:
                uow.users.update_user_timezone(user_id, canonical_tz)
                logger.info(
                    f"Opportunistic timezone update for {user_id}: UTC → {canonical_tz}"
                )
            except Exception:
                pass
        return canonical_tz

    # 3. Fallback
    return db_tz


def user_today(user_timezone: str = "UTC") -> date:
    """Return today's date in user's timezone."""
    tz = get_zone_info(user_timezone)
    return datetime.now(tz).date()


def noon_utc_for_date(target_date: date, user_timezone: str = "UTC") -> datetime:
    """Return a UTC datetime at noon in the user's local timezone for the given date.

    When logging meals for a specific date, using the current UTC time can place
    the ``created_at`` outside the date boundary in the user's timezone (e.g.
    UTC 17:30 is already March 24 in Asia/Saigon).  Setting the time to local
    noon guarantees the stored UTC value falls within the correct date boundary
    for any timezone from UTC-12 to UTC+14.
    """
    tz = get_zone_info(user_timezone)
    local_noon = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        12,
        0,
        0,
        tzinfo=tz,
    )
    return local_noon.astimezone(UTC)
