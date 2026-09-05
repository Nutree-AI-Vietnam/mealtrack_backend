from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.user.update_custom_macros_command import UpdateCustomMacrosCommand
from src.app.handlers.command_handlers.update_custom_macros_command_handler import (
    UpdateCustomMacrosCommandHandler,
)


def _uow_for(profile):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value.first.return_value = profile
    uow.session.execute = AsyncMock(return_value=result)
    return uow


@pytest.mark.asyncio
async def test_custom_macro_reset_increments_revision_once():
    profile = SimpleNamespace(
        custom_protein_g=120.0,
        custom_carbs_g=180.0,
        custom_fat_g=60.0,
        profile_target_revision=1,
    )
    publisher = MagicMock(publish=AsyncMock())

    with patch(
        "src.app.handlers.command_handlers.update_custom_macros_command_handler.AsyncUnitOfWork",
        return_value=_uow_for(profile),
    ):
        await UpdateCustomMacrosCommandHandler(event_publisher=publisher).handle(
            UpdateCustomMacrosCommand(user_id="u1")
        )

    assert (profile.custom_protein_g, profile.custom_carbs_g, profile.custom_fat_g) == (
        None,
        None,
        None,
    )
    assert profile.profile_target_revision == 2
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_custom_macro_reset_is_a_revision_noop():
    profile = SimpleNamespace(
        custom_protein_g=None,
        custom_carbs_g=None,
        custom_fat_g=None,
        profile_target_revision=1,
    )

    with patch(
        "src.app.handlers.command_handlers.update_custom_macros_command_handler.AsyncUnitOfWork",
        return_value=_uow_for(profile),
    ):
        await UpdateCustomMacrosCommandHandler(
            event_publisher=MagicMock(publish=AsyncMock())
        ).handle(UpdateCustomMacrosCommand(user_id="u1"))

    assert profile.profile_target_revision == 1
