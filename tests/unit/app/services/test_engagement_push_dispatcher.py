"""EngagementPushDispatcher sends due outbox pushes (payment failed)."""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_engagement_push import (
    EXPIRED,
    FAILED,
    NOW,
    PENDING,
    PROCESSING,
    SENT,
    USER_ID,
    FakeEngagementPushUow,
    FakePushSender,
)

from src.app.services.engagement_push import (
    EngagementPushDispatcher,
    build_engagement_push_message,
)
from src.domain.model.notification import (
    DeviceType,
    EngagementPushClaim,
    EngagementPushKind,
    PushMessage,
    PushSendReport,
    PushTarget,
)

LOGGER = "src.app.services.engagement_push.dispatcher"
TARGETS = [
    PushTarget(token="ios-token", device_type=DeviceType.IOS),
    PushTarget(token="android-token", device_type=DeviceType.ANDROID),
]
EN_TITLE = "Payment didn't go through"
EN_BODY = "Update your payment method to keep your Nutree plan running."


@pytest.fixture
def metrics():
    with patch("src.app.services.engagement_push.dispatcher.increment_metric") as m:
        yield m


@pytest.fixture
def uow() -> FakeEngagementPushUow:
    uow = FakeEngagementPushUow()
    uow.push_tokens.list_active_targets.return_value = list(TARGETS)
    return uow


@pytest.fixture
def sender() -> FakePushSender:
    return FakePushSender()


def _dispatcher(uow, sender, **kwargs) -> EngagementPushDispatcher:
    return EngagementPushDispatcher(uow_factory=uow, sender=sender, **kwargs)


def _outcomes(metrics) -> list[str]:
    return [
        call.kwargs["attributes"]["outcome"]
        for call in metrics.call_args_list
        if call.args[0] == "engagement_push.dispatch"
    ]


def _claim(**context) -> EngagementPushClaim:
    return EngagementPushClaim(
        id="push-1",
        user_id=USER_ID,
        kind=EngagementPushKind.BILLING_ISSUE,
        context=context,
    )


# --- build_engagement_push_message ---


def test_message_deep_links_to_the_profile():
    message = build_engagement_push_message(_claim(language="en"))

    assert message == PushMessage(
        title=EN_TITLE,
        body=EN_BODY,
        data={"type": "engagement", "kind": "billing_issue", "route": "/profile"},
        collapse_key="billing_issue",
    )
    # FCM rejects data payloads with non-string values.
    assert all(isinstance(value, str) for value in message.data.values())


def test_message_uses_the_stored_language():
    message = build_engagement_push_message(_claim(language="vi"))

    assert (message.title, message.body) == (
        "Thanh toán chưa thành công",
        "Cập nhật phương thức thanh toán để gói Nutree không bị gián đoạn nhé.",
    )


@pytest.mark.parametrize("context", [{}, {"language": 7}, {"language": "ko"}])
def test_message_without_a_usable_language_is_english(context):
    message = build_engagement_push_message(_claim(**context))

    assert (message.title, message.body) == (EN_TITLE, EN_BODY)


# --- run_once / deliver ---


@pytest.mark.asyncio
async def test_due_push_is_sent_and_marked_sent(uow, sender, metrics):
    row = uow.engagement_pushes.add(context={"kind": "billing_issue", "language": "vi"})

    claimed = await _dispatcher(uow, sender).run_once()

    assert claimed == 1
    assert uow.engagement_pushes.status(row.id) == SENT
    uow.push_tokens.list_active_targets.assert_awaited_once_with(user_id=USER_ID)
    [(targets, message)] = sender.calls
    assert targets == TARGETS
    assert message.data == {
        "type": "engagement",
        "kind": "billing_issue",
        "route": "/profile",
    }
    assert message.title == "Thanh toán chưa thành công"
    uow.push_tokens.deactivate_tokens.assert_not_awaited()
    metrics.assert_called_once_with(
        "engagement_push.dispatch",
        attributes={"kind": "billing_issue", "outcome": "sent"},
    )


@pytest.mark.asyncio
async def test_claim_commits_before_the_push_goes_out(uow, sender, metrics):
    uow.engagement_pushes.add()
    transactions_at_send = []
    statuses_at_send = []
    report = PushSendReport(sent=1)

    async def send(targets, message):
        transactions_at_send.append((uow.opened, uow.committed))
        statuses_at_send.extend(r.status for r in uow.engagement_pushes.rows.values())
        return report

    sender.send = send  # type: ignore[method-assign]

    await _dispatcher(uow, sender).run_once()

    # The claim and the device lookup each committed on their own.
    assert transactions_at_send == [(2, 2)]
    assert statuses_at_send == [PROCESSING]


@pytest.mark.asyncio
async def test_run_once_claims_one_batch(uow, sender, metrics):
    rows = [
        uow.engagement_pushes.add(
            user_id=f"user-{i}", scheduled_for_utc=NOW - timedelta(minutes=10 - i)
        )
        for i in range(3)
    ]

    claimed = await _dispatcher(uow, sender, batch_size=2).run_once()

    assert claimed == 2
    assert uow.engagement_pushes.claim_limits == [2]
    assert [uow.engagement_pushes.status(row.id) for row in rows] == [
        SENT,
        SENT,
        PENDING,
    ]


@pytest.mark.asyncio
async def test_default_batch_is_twenty(uow, sender, metrics):
    await _dispatcher(uow, sender).run_once()

    assert uow.engagement_pushes.claim_limits == [20]


@pytest.mark.asyncio
async def test_push_not_due_yet_stays_pending(uow, sender, metrics):
    row = uow.engagement_pushes.add(scheduled_for_utc=NOW + timedelta(hours=3))

    claimed = await _dispatcher(uow, sender).run_once()

    assert claimed == 0
    assert uow.engagement_pushes.status(row.id) == PENDING
    assert sender.calls == []


@pytest.mark.asyncio
async def test_user_without_a_device_is_marked_failed(uow, sender, metrics):
    uow.push_tokens.list_active_targets.return_value = []
    row = uow.engagement_pushes.add()

    await _dispatcher(uow, sender).run_once()

    assert uow.engagement_pushes.status(row.id) == FAILED
    assert sender.calls == []
    assert _outcomes(metrics) == ["no_device"]


@pytest.mark.asyncio
async def test_invalid_tokens_are_deactivated_and_the_push_still_counts(
    uow, sender, metrics
):
    sender.report = PushSendReport(sent=1, failed=1, invalid_tokens=("android-token",))
    row = uow.engagement_pushes.add()

    await _dispatcher(uow, sender).run_once()

    uow.push_tokens.deactivate_tokens.assert_awaited_once_with(("android-token",))
    assert uow.engagement_pushes.status(row.id) == SENT
    assert _outcomes(metrics) == ["sent"]


@pytest.mark.asyncio
async def test_only_invalid_tokens_marks_the_push_failed(uow, sender, metrics):
    sender.report = PushSendReport(
        failed=2, invalid_tokens=("ios-token", "android-token")
    )
    row = uow.engagement_pushes.add()

    await _dispatcher(uow, sender).run_once()

    uow.push_tokens.deactivate_tokens.assert_awaited_once_with(
        ("ios-token", "android-token")
    )
    assert uow.engagement_pushes.status(row.id) == FAILED
    assert _outcomes(metrics) == ["failed"]


@pytest.mark.asyncio
async def test_rejected_push_is_marked_failed_without_a_retry(uow, sender, metrics):
    sender.report = PushSendReport(failed=2, retry_later=True)
    row = uow.engagement_pushes.add()

    await _dispatcher(uow, sender).run_once()

    assert uow.engagement_pushes.status(row.id) == FAILED
    uow.push_tokens.deactivate_tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivation_failure_does_not_change_the_outcome(
    uow, sender, metrics, caplog
):
    sender.report = PushSendReport(sent=1, invalid_tokens=("android-token",))
    uow.push_tokens.deactivate_tokens.side_effect = RuntimeError("db down")
    row = uow.engagement_pushes.add()

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await _dispatcher(uow, sender).run_once()

    assert uow.engagement_pushes.status(row.id) == SENT
    assert "Failed to deactivate rejected push tokens" in caplog.messages


@pytest.mark.asyncio
async def test_expired_pushes_are_never_sent(uow, sender, metrics):
    stale = uow.engagement_pushes.add(
        scheduled_for_utc=NOW - timedelta(hours=49),
        expires_at=NOW - timedelta(hours=1),
    )
    stuck = uow.engagement_pushes.add(
        status=PROCESSING,
        scheduled_for_utc=NOW - timedelta(hours=50),
        expires_at=NOW - timedelta(hours=2),
    )
    fresh = uow.engagement_pushes.add()

    claimed = await _dispatcher(uow, sender).run_once()

    assert claimed == 1
    assert uow.engagement_pushes.status(stale.id) == EXPIRED
    assert uow.engagement_pushes.status(stuck.id) == EXPIRED
    assert uow.engagement_pushes.status(fresh.id) == SENT
    assert len(sender.calls) == 1
    metrics.assert_any_call("engagement_push.expired", 2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["send", "device_lookup"])
async def test_delivery_error_marks_the_push_failed_and_the_batch_goes_on(
    uow, sender, metrics, caplog, failure
):
    first = uow.engagement_pushes.add(scheduled_for_utc=NOW - timedelta(minutes=5))
    second = uow.engagement_pushes.add(
        user_id="user-2", scheduled_for_utc=NOW - timedelta(minutes=1)
    )
    error = RuntimeError("down")
    if failure == "send":
        results = iter([error, PushSendReport(sent=1)])

        async def send(targets, message):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        sender.send = send  # type: ignore[method-assign]
    else:
        uow.push_tokens.list_active_targets.side_effect = [error, list(TARGETS)]

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        claimed = await _dispatcher(uow, sender).run_once()

    assert claimed == 2
    assert uow.engagement_pushes.status(first.id) == FAILED
    assert uow.engagement_pushes.status(second.id) == SENT
    assert _outcomes(metrics) == ["error", "sent"]
    [record] = [r for r in caplog.records if r.getMessage() == "Engagement push failed"]
    assert (record.push_id, record.kind) == (first.id, "billing_issue")


@pytest.mark.asyncio
async def test_a_message_that_cannot_be_built_is_marked_failed(
    uow, sender, metrics, caplog
):
    row = uow.engagement_pushes.add()

    with (
        patch(
            "src.app.services.engagement_push.dispatcher.engagement_push_copy",
            side_effect=KeyError("no copy"),
        ),
        caplog.at_level(logging.ERROR, logger=LOGGER),
    ):
        claimed = await _dispatcher(uow, sender).run_once()

    assert claimed == 1
    assert uow.engagement_pushes.status(row.id) == FAILED
    assert sender.calls == []
    assert _outcomes(metrics) == ["error"]


@pytest.mark.asyncio
async def test_a_push_that_cannot_be_settled_stays_processing(
    uow, sender, metrics, caplog
):
    row = uow.engagement_pushes.add()

    async def broken(push_id):
        raise RuntimeError("db down")

    uow.engagement_pushes.mark_sent = broken  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        claimed = await _dispatcher(uow, sender).run_once()

    # Never sent twice: the row is left for expiry instead of going back.
    assert claimed == 1
    assert uow.engagement_pushes.status(row.id) == PROCESSING
    assert len(sender.calls) == 1
    assert "Engagement push failed" in caplog.messages


# --- run ---


@pytest.mark.asyncio
async def test_run_returns_at_once_when_already_stopped(uow, sender, metrics):
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(_dispatcher(uow, sender).run(stop), 1)

    assert uow.opened == 0


@pytest.mark.asyncio
async def test_run_survives_a_failed_pass(uow, sender, metrics, caplog):
    stop = asyncio.Event()
    passes = []
    outbox = uow.engagement_pushes

    async def expire_stale():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("db down")
        stop.set()
        return 0

    outbox.expire_stale = expire_stale  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await asyncio.wait_for(
            _dispatcher(uow, sender, interval_seconds=0.001).run(stop), 1
        )

    assert len(passes) == 2
    assert "Engagement push dispatcher pass failed" in caplog.messages


@pytest.mark.asyncio
async def test_run_goes_straight_to_the_next_batch_when_one_is_full(
    uow, sender, metrics
):
    stop = asyncio.Event()
    for i in range(3):
        uow.engagement_pushes.add(
            user_id=f"user-{i}", scheduled_for_utc=NOW - timedelta(minutes=10 - i)
        )
    outbox = uow.engagement_pushes
    claim_due = outbox.claim_due

    async def claim(*, limit):
        claims = await claim_due(limit=limit)
        if len(claims) < limit:
            stop.set()
        return claims

    outbox.claim_due = claim  # type: ignore[method-assign]
    # A 30 s wait between the batches would trip the 1 s timeout.
    dispatcher = _dispatcher(uow, sender, batch_size=2, interval_seconds=30)

    await asyncio.wait_for(dispatcher.run(stop), 1)

    assert outbox.claim_limits == [2, 2]
    assert len(sender.calls) == 3


@pytest.mark.asyncio
async def test_run_wakes_from_its_wait_when_stopped(uow, sender, metrics):
    stop = asyncio.Event()
    claimed = asyncio.Event()
    outbox = uow.engagement_pushes
    claim_due = outbox.claim_due

    async def claim(*, limit):
        claimed.set()
        return await claim_due(limit=limit)

    outbox.claim_due = claim  # type: ignore[method-assign]
    task = asyncio.create_task(_dispatcher(uow, sender, interval_seconds=30).run(stop))
    await asyncio.wait_for(claimed.wait(), 1)

    stop.set()
    await asyncio.wait_for(task, 1)

    assert outbox.claim_limits == [20]
