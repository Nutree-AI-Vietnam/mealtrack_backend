"""Registers the devices that receive AI job pushes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.app.services.ai_jobs.signals import AiJobsSchemaProbe
from src.domain.model.notification import DeviceType


class PushTokenService:
    """Token writes work whether or not AI jobs are enabled, so devices are
    already registered when the feature is switched on."""

    def __init__(
        self, *, uow_factory: Callable[[], Any], schema_probe: AiJobsSchemaProbe
    ) -> None:
        self._uow_factory = uow_factory
        self._probe = schema_probe

    async def register(
        self, *, user_id: str, token: str, device_type: DeviceType
    ) -> bool:
        """Return False when token storage is not migrated yet."""
        if not await self._probe.ready():
            return False
        async with self._uow_factory() as uow:
            await uow.push_tokens.upsert(
                user_id=user_id, token=token, device_type=device_type
            )
        return True

    async def unregister(self, *, user_id: str, token: str) -> bool:
        """Return False when token storage is not migrated yet."""
        if not await self._probe.ready():
            return False
        async with self._uow_factory() as uow:
            await uow.push_tokens.deactivate(user_id=user_id, token=token)
        return True
