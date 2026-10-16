"""``ent_ledger_write`` OFF: the RevenueCat webhook behaves exactly as before.

Re-runs every existing webhook route and handler scenario with the flag
explicitly off, ``ent_reconcile`` on (it must not matter without the ledger)
and a guard that fails the test if any ledger code is reached.
"""

import pytest
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader
from tests.unit.api.test_revenuecat_webhook_auth import (
    TestWebhookAuthPrecedence as _LegacyAuthPrecedence,
)
from tests.unit.api.test_revenuecat_webhook_auth import (
    TestWebhookDeliveryMetrics as _LegacyDeliveryMetrics,
)
from tests.unit.api.test_revenuecat_webhook_auth import (
    unparseable_request,  # noqa: F401 - fixture used by the inherited tests
)
from tests.unit.api.test_webhook_handler import (
    TestWebhookHandler as _LegacyWebhookHandler,
)

from src.api.routes.v1 import webhooks
from src.domain.model.billing.constants import FLAG_ENT_LEDGER_WRITE, FLAG_ENT_RECONCILE


def _ledger_must_not_be_used():
    raise AssertionError("ledger code reached with ent_ledger_write off")


@pytest.fixture(autouse=True)
def ledger_flag_off(monkeypatch):
    reader = StaticFeatureFlagReader(
        {FLAG_ENT_LEDGER_WRITE: False, FLAG_ENT_RECONCILE: True}
    )
    monkeypatch.setattr(webhooks, "_get_feature_flag_reader", lambda: reader)
    monkeypatch.setattr(
        webhooks, "_get_entitlement_inbox_service", _ledger_must_not_be_used
    )
    yield reader
    # Only the ledger switch itself is ever consulted on the legacy path.
    assert set(reader.reads) <= {FLAG_ENT_LEDGER_WRITE}


@pytest.mark.asyncio
class TestWebhookHandlerFlagOff(_LegacyWebhookHandler):
    """Every legacy route and handler scenario, flag off."""


@pytest.mark.asyncio
class TestWebhookAuthPrecedenceFlagOff(_LegacyAuthPrecedence):
    pass


@pytest.mark.asyncio
class TestWebhookDeliveryMetricsFlagOff(_LegacyDeliveryMetrics):
    pass


@pytest.mark.asyncio
async def test_flag_is_read_on_the_native_path(ledger_flag_off, monkeypatch):
    """Sanity check that the parity runs above really exercised the flag read."""
    from unittest.mock import AsyncMock, MagicMock, patch

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "event": {"type": "BILLING_ISSUE", "app_user_id": "$RCAnonymousID:x"}
        }
    )
    with (
        patch.multiple(
            "src.api.routes.v1.webhooks.settings",
            REVENUECAT_WEBHOOK_AUTH_HEADER="s",
            REVENUECAT_WEBHOOK_SECRET="s",
        ),
        patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as uow_class,
    ):
        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.subscriptions.find_by_revenuecat_id = AsyncMock(return_value=None)
        result = MagicMock()
        result.scalars.return_value.first.return_value = None
        uow.session.execute = AsyncMock(return_value=result)
        uow_class.return_value = uow

        response = await webhooks.revenuecat_webhook(request, authorization="s")

    assert response == {"status": "ignored", "reason": "user_not_found"}
    assert ledger_flag_off.reads == [FLAG_ENT_LEDGER_WRITE]
