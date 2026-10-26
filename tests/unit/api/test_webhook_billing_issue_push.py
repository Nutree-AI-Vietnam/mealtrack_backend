"""RevenueCat lifecycle webhooks queue and drop the payment-failed push."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from src.api.routes.v1.webhooks import (
    handle_billing_issue,
    handle_cancellation,
    handle_expiration,
    handle_product_change,
    handle_purchase,
    handle_refund,
    handle_renewal,
    revenuecat_webhook,
)
from src.domain.model.notification import EngagementPushKind, NewEngagementPush
from src.domain.ports.engagement_push_repository_port import (
    EngagementPushRepositoryPort,
)

LIFECYCLE = "src.api.routes.v1.webhook_subscription_lifecycle"
# 22:00 in Ho Chi Minh City, after the 09:00-20:00 window has closed.
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
EVENT = {"app_user_id": "user_123", "product_id": "premium_monthly"}


def _uow():
    uow = AsyncMock()
    uow.session = MagicMock()
    uow.session.execute = AsyncMock()
    uow.engagement_pushes = create_autospec(EngagementPushRepositoryPort, instance=True)
    uow.engagement_pushes.enqueue.return_value = True
    uow.engagement_pushes.delete_pending.return_value = 1
    return uow


def _user(**overrides):
    values = {"id": "user_123", "timezone": "Asia/Ho_Chi_Minh", "language_code": "vi"}
    return SimpleNamespace(**{**values, **overrides})


def _publisher():
    return MagicMock(publish=AsyncMock())


@pytest.fixture
def push_enabled():
    with patch(f"{LIFECYCLE}.engagement_push_enabled", return_value=True) as flag:
        yield flag


@pytest.fixture
def clock():
    with patch(f"{LIFECYCLE}.utc_now", return_value=NOW):
        yield


def _subscription(status: str = "active"):
    return SimpleNamespace(
        status=status,
        product_id="premium_monthly",
        expires_at=None,
        updated_at=None,
        cancelled_at=None,
    )


async def _billing_issue(uow, user, subscription):
    with patch(
        f"{LIFECYCLE}.get_or_create_subscription",
        new_callable=AsyncMock,
        return_value=subscription,
    ):
        await handle_billing_issue(uow, user, dict(EVENT), event_publisher=_publisher())


# --- BILLING_ISSUE queues the push ---


@pytest.mark.asyncio
async def test_billing_issue_queues_one_push_for_the_next_local_window(
    push_enabled, clock
):
    uow = _uow()
    subscription = _subscription()

    await _billing_issue(uow, _user(), subscription)

    assert subscription.status == "billing_issue"
    [push] = uow.engagement_pushes.enqueue.await_args.args
    assert isinstance(push, NewEngagementPush)
    assert push.kind is EngagementPushKind.BILLING_ISSUE
    assert push.user_id == "user_123"
    assert push.scheduled_for_utc == datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
    assert push.scheduled_date == date(2026, 10, 27)
    assert push.expires_at == datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
    assert push.context == {"kind": "billing_issue", "language": "vi"}
    # The write runs in a savepoint so a failure cannot abort the webhook.
    uow.session.begin_nested.assert_called_once_with()


@pytest.mark.asyncio
async def test_user_without_timezone_or_language_gets_utc_and_english(
    push_enabled, clock
):
    uow = _uow()
    user = MagicMock(id="user_123")  # attributes are not strings

    await _billing_issue(uow, user, _subscription())

    [push] = uow.engagement_pushes.enqueue.await_args.args
    assert push.scheduled_for_utc == NOW  # 15:00 UTC is inside the window
    assert push.scheduled_date == date(2026, 10, 26)
    assert push.context == {"kind": "billing_issue", "language": "en"}


@pytest.mark.asyncio
async def test_nothing_is_queued_while_the_flag_is_off(clock):
    uow = _uow()
    subscription = _subscription()

    with patch(f"{LIFECYCLE}.engagement_push_enabled", return_value=False):
        await _billing_issue(uow, _user(), subscription)

    assert subscription.status == "billing_issue"
    uow.engagement_pushes.enqueue.assert_not_awaited()
    uow.session.begin_nested.assert_not_called()


@pytest.mark.asyncio
async def test_repeat_billing_issue_in_the_same_episode_queues_nothing(
    push_enabled, clock
):
    uow = _uow()

    await _billing_issue(uow, _user(), _subscription(status="billing_issue"))

    uow.engagement_pushes.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_enqueue_never_fails_the_webhook(push_enabled, clock, caplog):
    uow = _uow()
    uow.engagement_pushes.enqueue.side_effect = RuntimeError("db down")
    publisher = _publisher()
    subscription = _subscription()

    with (
        patch(
            f"{LIFECYCLE}.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=subscription,
        ),
        caplog.at_level("ERROR", logger=LIFECYCLE),
    ):
        await handle_billing_issue(uow, _user(), dict(EVENT), event_publisher=publisher)

    assert subscription.status == "billing_issue"
    publisher.publish.assert_awaited_once()
    assert "Failed to queue the billing-issue push for user user_123" in caplog.messages


# --- resolution drops the pending push ---


def _assert_dropped(uow) -> None:
    uow.engagement_pushes.delete_pending.assert_awaited_once_with(
        user_id="user_123", kind=EngagementPushKind.BILLING_ISSUE
    )


@pytest.mark.asyncio
async def test_renewal_drops_the_push_and_still_purges_trial_pushes():
    uow = _uow()
    subscription = _subscription(status="billing_issue")

    with patch(
        f"{LIFECYCLE}.get_subscription_by_revenuecat_id",
        new_callable=AsyncMock,
        return_value=subscription,
    ):
        await handle_renewal(
            uow,
            _user(),
            {**EVENT, "expiration_at_ms": 1699478400000},
            event_publisher=_publisher(),
        )

    assert subscription.status == "active"
    _assert_dropped(uow)
    [statement, params] = uow.session.execute.await_args.args
    assert "trial_expiry" in str(statement)
    assert params == {"uid": "user_123"}


@pytest.mark.asyncio
async def test_failed_drop_never_fails_the_webhook(caplog):
    uow = _uow()
    uow.engagement_pushes.delete_pending.side_effect = RuntimeError("db down")
    subscription = _subscription(status="billing_issue")

    with (
        patch(
            f"{LIFECYCLE}.get_subscription_by_revenuecat_id",
            new_callable=AsyncMock,
            return_value=subscription,
        ),
        caplog.at_level("ERROR", logger=LIFECYCLE),
    ):
        await handle_renewal(uow, _user(), dict(EVENT), event_publisher=_publisher())

    assert subscription.status == "active"
    assert "Failed to drop the billing-issue push for user user_123" in caplog.messages


@pytest.mark.asyncio
async def test_new_purchase_drops_the_push():
    uow = _uow()

    with (
        patch(
            f"{LIFECYCLE}.get_subscription_by_revenuecat_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(f"{LIFECYCLE}.credit_referral_on_purchase", new_callable=AsyncMock),
    ):
        await handle_purchase(uow, _user(), dict(EVENT), event_publisher=_publisher())

    _assert_dropped(uow)


@pytest.mark.asyncio
async def test_purchase_of_a_known_subscription_drops_the_push_once():
    uow = _uow()

    with patch(
        f"{LIFECYCLE}.get_subscription_by_revenuecat_id",
        new_callable=AsyncMock,
        return_value=_subscription(status="billing_issue"),
    ):
        await handle_purchase(uow, _user(), dict(EVENT), event_publisher=_publisher())

    _assert_dropped(uow)


@pytest.mark.asyncio
async def test_product_change_drops_the_push():
    uow = _uow()

    with patch(
        f"{LIFECYCLE}.get_or_create_subscription",
        new_callable=AsyncMock,
        return_value=_subscription(status="billing_issue"),
    ):
        await handle_product_change(
            uow, _user(), dict(EVENT), event_publisher=_publisher()
        )

    _assert_dropped(uow)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", [handle_cancellation, handle_expiration, handle_refund]
)
async def test_events_that_do_not_resolve_the_issue_keep_the_push(handler):
    uow = _uow()

    with (
        patch(
            f"{LIFECYCLE}.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=_subscription(status="billing_issue"),
        ),
        patch(f"{LIFECYCLE}.revoke_referral_on_refund", new_callable=AsyncMock),
    ):
        await handler(uow, _user(), dict(EVENT), event_publisher=_publisher())

    uow.engagement_pushes.delete_pending.assert_not_awaited()


# --- through the route ---


@pytest.mark.asyncio
async def test_billing_issue_webhook_queues_the_push(push_enabled, clock):
    uow = _uow()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": {"type": "BILLING_ISSUE", **EVENT}})

    with (
        patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"),
        patch("src.api.routes.v1.webhooks.AsyncUnitOfWork", return_value=uow),
        patch(
            "src.api.routes.v1.webhooks.record_web_funnel_redemption",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "src.api.routes.v1.webhooks.find_user_for_revenuecat_event",
            new_callable=AsyncMock,
            return_value=_user(),
        ),
        patch(
            "src.api.routes.v1.webhooks._get_event_publisher",
            return_value=_publisher(),
        ),
        patch(
            f"{LIFECYCLE}.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=_subscription(),
        ),
    ):
        result = await revenuecat_webhook(request, authorization="test_secret")

    assert result == {"status": "success"}
    [push] = uow.engagement_pushes.enqueue.await_args.args
    assert push.user_id == "user_123"
    assert push.kind is EngagementPushKind.BILLING_ISSUE
