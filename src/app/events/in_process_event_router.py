"""Run integration events inside the API process.

The publisher hands each committed event to the handlers registered here for
its ``event_type``. The best-effort publisher runs every publish on its own
background task and drains them at shutdown, so handlers never delay or fail
the write. Event types with no handler need no follow-up work: the write path
did it already.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

IntegrationEventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class InProcessIntegrationEventRouter:
    """Dispatch an event payload to every handler registered for its type."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[IntegrationEventHandler]] = {}

    def register(
        self, event_types: Iterable[str], handler: IntegrationEventHandler
    ) -> None:
        for event_type in event_types:
            self._handlers.setdefault(event_type, []).append(handler)

    def clear(self) -> None:
        self._handlers.clear()

    def handles(self, event_type: str) -> bool:
        return bool(self._handlers.get(event_type))

    async def publish(self, payload: dict[str, Any]) -> None:
        """Run the event's handlers in order; one failing does not skip the rest."""
        event_type = str(payload.get("event_type") or "")
        for handler in self._handlers.get(event_type, ()):
            try:
                await handler(payload)
            except Exception:
                logger.warning(
                    "in-process integration event handler failed",
                    extra={
                        "event_type": event_type,
                        "event_id": payload.get("event_id"),
                        "handler": getattr(handler, "__name__", type(handler).__name__),
                    },
                    exc_info=True,
                )


in_process_event_router = InProcessIntegrationEventRouter()
