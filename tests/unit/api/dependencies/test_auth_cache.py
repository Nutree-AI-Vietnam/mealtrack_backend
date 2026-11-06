"""Tests for in-memory auth UID cache."""

import pytest


@pytest.fixture(autouse=True)
def clear_uid_cache():
    """Isolate tests — wipe the module-level TTLCache between runs."""
    from src.api.dependencies import auth_cache as module

    module._uid_cache.clear()
    yield
    module._uid_cache.clear()


@pytest.mark.asyncio
async def test_get_returns_none_on_cache_miss():
    from src.api.dependencies.auth_cache import get_cached_user_id

    result = await get_cached_user_id(None, "uid-unknown")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_user_id_on_active_hit():
    from src.api.dependencies.auth_cache import get_cached_user_id, set_cached_user_id

    await set_cached_user_id(None, "uid-abc", "user-123", is_active=True)
    result = await get_cached_user_id(None, "uid-abc")
    assert result == "user-123"


@pytest.mark.asyncio
async def test_get_returns_none_for_inactive_user():
    from src.api.dependencies.auth_cache import get_cached_user_id, set_cached_user_id

    await set_cached_user_id(None, "uid-inactive", "user-456", is_active=False)
    result = await get_cached_user_id(None, "uid-inactive")
    assert result is None


@pytest.mark.asyncio
async def test_invalidate_removes_entry():
    from src.api.dependencies.auth_cache import (
        get_cached_user_id,
        invalidate_cached_user_id,
        set_cached_user_id,
    )

    await set_cached_user_id(None, "uid-del", "user-789", is_active=True)
    await invalidate_cached_user_id(None, "uid-del")
    result = await get_cached_user_id(None, "uid-del")
    assert result is None


@pytest.mark.asyncio
async def test_cache_service_arg_is_ignored():
    """cache_service=None must work — signature kept for backward compat only."""
    from src.api.dependencies.auth_cache import get_cached_user_id, set_cached_user_id

    await set_cached_user_id(None, "uid-x", "user-x", is_active=True)
    assert await get_cached_user_id(None, "uid-x") == "user-x"


@pytest.mark.asyncio
async def test_timezone_is_stored_with_the_auth_snapshot():
    from src.api.dependencies.auth_cache import (
        cached_user_timezone,
        set_cached_user_id,
    )

    await set_cached_user_id(
        None, "uid-tz", "user-tz", is_active=True, timezone="Asia/Ho_Chi_Minh"
    )

    assert cached_user_timezone("uid-tz") == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_timezone_update_refreshes_the_auth_snapshot():
    from src.api.dependencies.auth_cache import (
        cached_user_timezone,
        set_cached_user_id,
    )
    from src.domain.utils.request_user_timezone import publish_user_timezone_changed

    await set_cached_user_id(None, "uid-old", "user-old", True, timezone="UTC")
    publish_user_timezone_changed("user-old", "America/Chicago")

    assert cached_user_timezone("uid-old") == "America/Chicago"
