"""Shared fixtures: a SQLite unit of work, a publisher spy and a fake RevenueCat."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import USER, commit, open_subscription_uow

from src.domain.ports.subscription_service_port import EntitlementCheck

LIFECYCLE = "src.api.routes.v1.webhook_subscription_lifecycle"
LIFETIME = "src.api.routes.v1.webhook_lifetime_purchase"
FUNNEL = "src.api.routes.v1.webhook_referral_funnel"

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
    """RevenueCat answers: no renewing plan and no access unless a test sets them."""
    service = MagicMock(
        get_subscription_info=AsyncMock(return_value=None),
        check_entitlement=AsyncMock(return_value=EntitlementCheck.NOT_ENTITLED),
    )
    with (
        patch(f"{LIFETIME}._get_subscription_service", return_value=service),
        patch(f"{FUNNEL}._get_subscription_service", return_value=service),
    ):
        yield service


@pytest.fixture
def deliver(uow, publisher) -> Deliver:
    """Run one webhook handler as its own committed unit of work."""

    async def run(handler: Any, event: dict[str, Any], user: Any = USER) -> None:
        await handler(uow, user, event, event_publisher=publisher)
        commit(uow)

    return run
