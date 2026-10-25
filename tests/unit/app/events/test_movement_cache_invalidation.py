from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.events.movement.cache_invalidation import (
    invoke_movement_cache_invalidation_hook,
    register_movement_cache_invalidation_hook,
)


@pytest.mark.asyncio
async def test_invoke_movement_cache_invalidation_hook_when_registered():
    hook = AsyncMock()
    register_movement_cache_invalidation_hook(hook)
    try:
        await invoke_movement_cache_invalidation_hook("user-1", date(2026, 10, 20))
        hook.assert_awaited_once_with("user-1", date(2026, 10, 20))
    finally:
        register_movement_cache_invalidation_hook(None)


@pytest.mark.asyncio
async def test_invoke_movement_cache_invalidation_hook_when_none_is_noop():
    register_movement_cache_invalidation_hook(None)
    # Should not raise any error
    await invoke_movement_cache_invalidation_hook("user-1", date(2026, 10, 20))
