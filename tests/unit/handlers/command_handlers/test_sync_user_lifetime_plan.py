"""SyncUserCommandHandler reports a lifetime purchase as lifetime."""

from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.api.schemas.response.user_responses import SubscriptionInfo
from src.app.commands.user.sync_user_command import SyncUserCommand
from src.app.handlers.command_handlers.sync_user_command_handler import (
    SyncUserCommandHandler,
)
from src.domain.constants.lifetime_products import LIFETIME_UPGRADE_PRODUCT_ID
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.subscription import Subscription
from src.domain.model.user import UserDomainModel


@pytest.mark.asyncio
async def test_a_lifetime_purchase_reports_lifetime_and_no_plan_period():
    """A lifetime row has no expiry and reports lifetime, not a plan period."""
    fake_uow = FakeUnitOfWork()
    user_id = uuid4()
    await fake_uow.users.save(
        UserDomainModel(
            id=user_id,
            firebase_uid="firebase_lifetime",
            email="lifetime@example.com",
            username="lifetimeuser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
        )
    )
    await fake_uow.subscriptions.save(
        Subscription(
            user_id=str(user_id),
            product_id=LIFETIME_UPGRADE_PRODUCT_ID,
            status="active",
            expires_at=None,
            platform="android",
        )
    )

    result = await SyncUserCommandHandler(uow=fake_uow).handle(
        SyncUserCommand(
            firebase_uid="firebase_lifetime",
            email="lifetime@example.com",
            provider="google",
        )
    )

    assert result["user"]["has_subscription"] is True
    info = SubscriptionInfo(**result["user"]["subscription"])
    assert info.product_id == LIFETIME_UPGRADE_PRODUCT_ID
    assert (info.is_lifetime, info.is_monthly, info.is_yearly) == (True, False, False)
    assert info.expires_at is None
