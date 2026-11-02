"""Shared fixtures: a SQLite unit of work, a publisher spy and a fake RevenueCat."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import USER, commit, open_subscription_uow
from tests.fixtures.fakes.play_renewal_fakes import (
    LISTED_ORDERS,
    fake_play_service,
    revenuecat_subscriber,
)

from src.domain.ports.subscription_service_port import EntitlementCheck

LIFECYCLE = "src.api.routes.v1.webhook_subscription_lifecycle"
LIFETIME = "src.api.routes.v1.webhook_lifetime_purchase"
FUNNEL = "src.api.routes.v1.webhook_referral_funnel"
PLAY_CANCEL = "src.api.routes.v1.webhook_play_renewal_cancel"

Deliver = Callable[..., Awaitable[None]]


@pytest.fixture
def uow():
    with open_subscription_uow() as unit_of_work:
        yield unit_of_work


@pytest.fixture
def publisher():
    return MagicMock(publish=AsyncMock())


@pytest.fixture(autouse=True)
def revenuecat():
    """RevenueCat answers: no renewing plan and no access unless a test sets them.

    Its subscriber lists the test Play plans' orders, so the Play cancel can
    confirm their owner.
    """
    service = MagicMock(
        get_subscription_info=AsyncMock(return_value=None),
        check_entitlement=AsyncMock(return_value=EntitlementCheck.NOT_ENTITLED),
        get_subscriber_info=AsyncMock(
            return_value=revenuecat_subscriber(*LISTED_ORDERS)
        ),
    )
    with (
        patch(f"{LIFETIME}._get_subscription_service", return_value=service),
        patch(f"{FUNNEL}._get_subscription_service", return_value=service),
        patch(f"{PLAY_CANCEL}._get_subscription_service", return_value=service),
    ):
        yield service


@pytest.fixture(autouse=True)
def play():
    """An enabled fake Play adapter, so no test ever builds the real one."""
    service = fake_play_service()
    with patch(
        f"{PLAY_CANCEL}._get_play_subscription_service", return_value=service
    ) as getter:
        service.getter = getter
        yield service


@pytest.fixture
def deliver(uow, publisher) -> Deliver:
    """Run one webhook handler as its own committed unit of work."""

    async def run(handler: Any, event: dict[str, Any], user: Any = USER) -> None:
        await handler(uow, user, event, event_publisher=publisher)
        commit(uow)

    return run
