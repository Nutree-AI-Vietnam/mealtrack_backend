"""Composition root for supplement logging's server switch."""

from __future__ import annotations

from src.infra.config.settings import get_settings
from src.infra.database.uow_async import AsyncUnitOfWork


async def supplements_available() -> bool:
    """True once ``SUPPLEMENTS_ENABLED`` is on and the supplement tables exist."""
    if not get_settings().SUPPLEMENTS_ENABLED:
        return False
    async with AsyncUnitOfWork() as uow:
        return await uow.supplements.schema_ready()
