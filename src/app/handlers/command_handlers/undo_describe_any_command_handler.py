"""Hoàn tác for the + panel's Ghi nhanh: delete everything one sentence saved.

The sentence's replay record lists the ids it created per kind. One unit of
work deletes the meal, the drinks (their legacy meal rows retired), the
activities and the doses, and puts the supplement list back as it was. Then
each kind publishes and purges like its own delete route. An entry already
deleted by hand is skipped, so a repeated undo deletes nothing more.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.describe_any import UndoDescribeAnyCommand
from src.app.events.base import EventHandler, handles
from src.app.events.hydration.local_cache_invalidation import (
    invoke_local_hydration_cache_invalidation_hook,
)
from src.app.events.integration_event import IntegrationEvent
from src.app.events.meal.meal_events import invoke_local_cache_invalidation_hook
from src.app.events.movement.local_cache_invalidation import (
    invoke_local_movement_cache_invalidation_hook,
)
from src.app.handlers.command_handlers.delete_hydration_entry_command_handler import (
    remove_drink_entry,
)
from src.app.handlers.command_handlers.delete_meal_command_handler import remove_meal
from src.app.handlers.command_handlers.delete_movement_entry_command_handler import (
    remove_movement_entry,
)
from src.app.services.describe_any_items import ACTIVITY, DRINK, FOOD, SUPPLEMENT
from src.app.services.describe_log_replay import (
    LOG_DESCRIBE_OPERATION,
    created_entries,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

DayPurge = Callable[[str, date], Awaitable[None]]
_PURGES: dict[str, Callable[[str, date], Awaitable[None]]] = {
    DRINK: invoke_local_hydration_cache_invalidation_hook,
    ACTIVITY: invoke_local_movement_cache_invalidation_hook,
}


@handles(UndoDescribeAnyCommand)
class UndoDescribeAnyCommandHandler(EventHandler[UndoDescribeAnyCommand, dict]):
    def __init__(
        self,
        uow_factory: Any,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        purge_supplement_day: DayPurge | None = None,
    ):
        self.uow_factory = uow_factory
        self.event_publisher = event_publisher
        self.environment = environment
        self.purge_supplement_day = purge_supplement_day

    async def handle(self, cmd: UndoDescribeAnyCommand) -> dict:
        removed: dict[str, list[tuple[IntegrationEvent | None, date]]] = {
            FOOD: [],
            DRINK: [],
            ACTIVITY: [],
            SUPPLEMENT: [],
        }
        async with self.uow_factory() as uow:
            created = await created_entries(
                uow, cmd.user_id, LOG_DESCRIBE_OPERATION, cmd.request_id
            )
            if created is None:
                raise ResourceNotFoundException(
                    "Nothing was saved for this request",
                    "DESCRIBE_REQUEST_NOT_FOUND",
                )
            for kind, remove in (
                (FOOD, remove_meal),
                (DRINK, remove_drink_entry),
                (ACTIVITY, remove_movement_entry),
            ):
                for entry_id in created.get(kind) or []:
                    found = await remove(uow, cmd.user_id, entry_id, self.environment)
                    if found is not None:
                        removed[kind].append(found)
            for dose in created.get(SUPPLEMENT) or []:
                day = await _remove_dose(uow, cmd.user_id, dose)
                if day is not None:
                    removed[SUPPLEMENT].append((None, day))
        await self._after_commit(cmd.user_id, removed)
        return {
            "request_id": cmd.request_id,
            "deleted": {kind: len(found) for kind, found in removed.items()},
        }

    async def _after_commit(
        self,
        user_id: str,
        removed: dict[str, list[tuple[IntegrationEvent | None, date]]],
    ) -> None:
        work: list[Awaitable[None]] = []
        for kind, found in removed.items():
            for event, _ in found:
                if event is not None:
                    work.append(self._publish(event))
            for day in sorted({day for _, day in found}):
                work.append(self._purge(kind, user_id, day))
        for result in await asyncio.gather(*work, return_exceptions=True):
            if isinstance(result, Exception):
                logger.warning("Describe undo publish or purge failed", exc_info=result)

    async def _publish(self, event: IntegrationEvent) -> None:
        await require_event_publisher(self.event_publisher).publish(event.to_payload())

    async def _purge(self, kind: str, user_id: str, day: date) -> None:
        if kind == FOOD:
            await invoke_local_cache_invalidation_hook(user_id, day, None)
        elif kind == SUPPLEMENT:
            if self.purge_supplement_day is not None:
                await self.purge_supplement_day(user_id, day)
        else:
            await _PURGES[kind](user_id, day)


async def _remove_dose(uow: Any, user_id: str, dose: dict[str, Any]) -> date | None:
    """Delete the dose and put its list row back as it was before the sentence.

    A dose already gone leaves the list alone: the person may have added the
    product again since.
    """
    deleted = await uow.supplements.soft_delete_intake(user_id, dose["id"], utc_now())
    if deleted is None:
        return None
    if dose.get("listed_before") == "absent":
        await uow.supplements.remove_list_entry(user_id, dose["entry_id"])
    elif dose.get("listed_before") == "inactive":
        await uow.supplements.update_list_entry(user_id, dose["entry_id"], active=False)
    return deleted.local_date
