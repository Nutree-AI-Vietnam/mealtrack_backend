"""Handler for choosing an avatar that needs no upload."""

import logging
from collections.abc import Callable
from typing import Any

from src.api.exceptions import ValidationException
from src.app.commands.user.update_user_avatar_command import UpdateUserAvatarCommand
from src.app.events.base import EventHandler, handles
from src.app.services.user_avatar_writes import delete_avatar_image, store_user_avatar
from src.domain.model.user.user_avatar import UserAvatar
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.image_store_port import ImageStorePort

logger = logging.getLogger(__name__)


@handles(UpdateUserAvatarCommand)
class UpdateUserAvatarCommandHandler(
    EventHandler[UpdateUserAvatarCommand, dict[str, Any]]
):
    """Set auto, a character or an initials colour; drop a replaced photo."""

    def __init__(
        self,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        image_store: ImageStorePort | None = None,
    ):
        self.uow_factory = uow_factory
        self.image_store = image_store

    async def handle(self, command: UpdateUserAvatarCommand) -> dict[str, Any]:
        try:
            avatar = UserAvatar.chosen(
                command.kind, character=command.character, color=command.color
            )
        except ValueError as exc:
            raise ValidationException(
                message=str(exc), error_code="INVALID_AVATAR"
            ) from exc

        previous_image_id = await store_user_avatar(
            self.uow_factory, command.user_id, avatar
        )
        if previous_image_id:
            await delete_avatar_image(
                self.image_store, previous_image_id, reason="avatar replaced"
            )
        logger.info("Updated avatar for user %s: kind=%s", command.user_id, avatar.kind)
        return avatar.to_public_dict()
