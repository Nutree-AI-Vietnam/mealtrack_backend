"""Port for delivering push notifications to devices."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.domain.model.notification import PushMessage, PushSendReport, PushTarget


class PushSenderPort(ABC):
    @abstractmethod
    async def send(
        self, targets: Sequence[PushTarget], message: PushMessage
    ) -> PushSendReport:
        """Send one message to every target; never raises for per-token errors."""
