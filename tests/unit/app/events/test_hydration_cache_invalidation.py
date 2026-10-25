from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.events.hydration.cache_invalidation import (
    invoke_hydration_cache_invalidation_hook,
    register_hydration_cache_invalidation_hook,
)


@pytest.mark.asyncio
async def test_invoke_hydration_cache_invalidation_hook_when_registered():
    hook = AsyncMock()
    register_hydration_cache_invalidation_hook(hook)
    try:
        await invoke_hydration_cache_invalidation_hook("user-1", date(2026, 10, 20))
        hook.assert_awaited_once_with("user-1", date(2026, 10, 20))
    finally:
        register_hydration_cache_invalidation_hook(None)


@pytest.mark.asyncio
async def test_invoke_hydration_cache_invalidation_hook_when_none_is_noop():
    register_hydration_cache_invalidation_hook(None)
    # Should not raise any error
    await invoke_hydration_cache_invalidation_hook("user-1", date(2026, 10, 20))
