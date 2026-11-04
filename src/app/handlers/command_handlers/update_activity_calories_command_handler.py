"""Handler for the activity calories preference."""

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from src.app.commands.user.update_activity_calories_command import (
    UpdateActivityCaloriesCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import UserProfileUpdatedEvent
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.services.daily_target_snapshot_service import forget_today_snapshot
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async

logger = logging.getLogger(__name__)

# Purges the user's cached totals for the local day the switch changed on.
CachePurge = Callable[[str, date], Awaitable[None]]


@handles(UpdateActivityCaloriesCommand)
class UpdateActivityCaloriesCommandHandler(
    EventHandler[UpdateActivityCaloriesCommand, dict[str, Any]]
):
    """Persist the activity calories preference. Enabled is the default.

    The switch changes the budget math for every day, so a change also drops
    today's stored target and purges cached totals before it returns.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        purge_caches: CachePurge | None = None,
        **kwargs: Any,
    ):
        self.uow_factory = uow_factory
        self.event_publisher = event_publisher
        self.environment = environment
        self.purge_caches = purge_caches

    def set_dependencies(self, **kwargs):
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(self, command: UpdateActivityCaloriesCommand) -> dict[str, Any]:
        enabled = bool(command.enabled)
        user_id = command.user_id

        async with self.uow_factory() as uow:
            current = await uow.users.get_activity_calories(user_id)
            if current == enabled:
                return {"enabled": enabled}
            await uow.users.update_user_activity_calories(user_id, enabled)
            user_tz = await resolve_user_timezone_async(user_id, uow)
            today = datetime.now(get_zone_info(user_tz)).date()
            # Today's stored target was computed under the old setting; the
            # next read stores it again under the new one.
            await forget_today_snapshot(uow, user_id, today)
            await uow.commit()

        if self.purge_caches is not None:
            await self.purge_caches(user_id, today)
        event = UserProfileUpdatedEvent(
            environment=self.environment,
            aggregate_id=user_id,
            data={"user_id": user_id, "activity_calories": enabled},
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info("Updated activity calories for user %s: %s", user_id, enabled)
        return {"enabled": enabled}
