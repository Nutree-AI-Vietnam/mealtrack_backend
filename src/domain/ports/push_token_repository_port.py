"""Persistence port for device push tokens."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.domain.model.notification import DeviceType, PushTarget


class PushTokenRepositoryPort(ABC):
    @abstractmethod
    async def upsert(
        self, *, user_id: str, token: str, device_type: DeviceType
    ) -> None:
        """Register the token for the user, taking it over from any other user."""

    @abstractmethod
    async def deactivate(self, *, user_id: str, token: str) -> bool:
        """Stop sending to the user's token; False if they do not own it."""

    @abstractmethod
    async def list_active_targets(self, *, user_id: str) -> list[PushTarget]:
        """Return the user's active tokens that were registered by this API."""

    @abstractmethod
    async def deactivate_tokens(self, tokens: Sequence[str]) -> int:
        """Deactivate tokens the push provider rejected as permanently invalid."""
