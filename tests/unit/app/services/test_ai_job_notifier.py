"""AiJobNotifier pushes finished jobs the user has not seen in the app."""

import asyncio
import logging
from unittest.mock import create_autospec, patch

import pytest
from tests.fixtures.fakes.fake_ai_jobs import (
    JOB_ID,
    USER_ID,
    FakeAiJobsUow,
    StubSchemaProbe,
    make_notification_claim,
)

from src.app.services.ai_jobs.notifier import AiJobNotifier, build_ai_job_push_message
from src.domain.model.ai_jobs import AiJobStatus, AiJobType
from src.domain.model.notification import (
    DeviceType,
    PushMessage,
    PushSendReport,
    PushTarget,
)
from src.domain.ports.push_sender_port import PushSenderPort

NOTIFIER_LOGGER = "src.app.services.ai_jobs.notifier"
TARGETS = [
    PushTarget(token="ios-token", device_type=DeviceType.IOS),
    PushTarget(token="android-token", device_type=DeviceType.ANDROID),
]


@pytest.fixture
def metrics():
    with patch("src.app.services.ai_jobs.notifier.increment_metric") as metric:
        yield metric


@pytest.fixture
def uow():
    uow = FakeAiJobsUow()
    uow.ai_jobs.claim_notifications.return_value = []
    uow.ai_jobs.release_notification.return_value = None
    uow.push_tokens.list_active_targets.return_value = list(TARGETS)
    uow.push_tokens.deactivate_tokens.return_value = 0
    return uow


@pytest.fixture
def sender():
    sender = create_autospec(PushSenderPort, instance=True)
    sender.send.return_value = PushSendReport(sent=1)
    return sender


def _notifier(uow: FakeAiJobsUow, sender, **kwargs) -> AiJobNotifier:
    return AiJobNotifier(uow_factory=uow, sender=sender, **kwargs)


def _outcomes(metrics) -> list[str]:
    return [call.kwargs["attributes"]["outcome"] for call in metrics.call_args_list]


def _failed_claim(**overrides):
    return make_notification_claim(
        **{
            "status": AiJobStatus.FAILED,
            "result": None,
            "error_code": "AI_UNAVAILABLE",
            "error_retryable": True,
            **overrides,
        }
    )


# --- build_ai_job_push_message ---


@pytest.mark.parametrize(
    "status", [AiJobStatus.QUEUED, AiJobStatus.RUNNING, AiJobStatus.CANCELLED]
)
def test_unfinished_and_cancelled_jobs_have_no_push(status):
    assert build_ai_job_push_message(make_notification_claim(status=status)) is None


def test_completed_meal_scan_push_deep_links_to_the_meal():
    message = build_ai_job_push_message(make_notification_claim())

    assert message == PushMessage(
        title="Your meal is ready",
        body="Tap to see the nutrition breakdown.",
        data={
            "type": "ai_job",
            "job_id": JOB_ID,
            "job_type": "meal_scan",
            "status": "completed",
            "meal_id": "meal-1",
        },
        thread_id="ai_job",
        collapse_key=JOB_ID,
    )


@pytest.mark.parametrize(
    "result",
    [None, {}, {"meal_id": ""}, {"meal_id": None}, {"meal_id": 42}],
    ids=["no-result", "empty", "blank", "none", "not-a-string"],
)
def test_completed_meal_scan_without_a_usable_meal_id_has_no_deep_link(result):
    message = build_ai_job_push_message(make_notification_claim(result=result))

    assert message.data == {
        "type": "ai_job",
        "job_id": JOB_ID,
        "job_type": "meal_scan",
        "status": "completed",
    }


@pytest.mark.parametrize(
    ("job_type", "title", "body"),
    [
        (AiJobType.PARSE_TEXT, "Your meal is ready", "Tap to review and log it."),
        (AiJobType.CHAT, "Your coach replied", "Tap to read the answer."),
    ],
)
def test_other_completed_jobs_never_carry_a_meal_id(job_type, title, body):
    claim = make_notification_claim(job_type=job_type, result={"meal_id": "meal-1"})

    message = build_ai_job_push_message(claim)

    assert (message.title, message.body) == (title, body)
    assert message.data == {
        "type": "ai_job",
        "job_id": JOB_ID,
        "job_type": job_type.value,
        "status": "completed",
    }


def test_failed_push_carries_the_error_code_and_no_meal_id():
    message = build_ai_job_push_message(_failed_claim(result={"meal_id": "meal-1"}))

    assert message == PushMessage(
        title="We couldn't analyze your meal",
        body="Tap to try again.",
        data={
            "type": "ai_job",
            "job_id": JOB_ID,
            "job_type": "meal_scan",
            "status": "failed",
            "error_code": "AI_UNAVAILABLE",
        },
        thread_id="ai_job",
        collapse_key=JOB_ID,
    )


def test_failed_push_without_an_error_code_leaves_it_out():
    message = build_ai_job_push_message(_failed_claim(error_code=None))

    assert "error_code" not in message.data
    assert message.data["status"] == "failed"


@pytest.mark.parametrize(
    ("retryable", "body"),
    [
        (True, "Tap to try again."),
        (None, "Tap to try again."),
        (False, "Tap to see what happened."),
    ],
    ids=["retryable", "unknown-counts-as-retryable", "permanent"],
)
def test_failed_push_body_says_whether_the_user_can_retry(retryable, body):
    message = build_ai_job_push_message(_failed_claim(error_retryable=retryable))

    assert message.body == body


@pytest.mark.parametrize("language", ["vi", "vi-VN", "VI"])
def test_vietnamese_copy(language):
    completed = build_ai_job_push_message(make_notification_claim(language=language))
    failed = build_ai_job_push_message(
        _failed_claim(
            job_type=AiJobType.PARSE_TEXT,
            language=language,
            error_code="INVALID_JOB_INPUT",
            error_retryable=False,
        )
    )

    assert (completed.title, completed.body) == (
        "Bữa ăn của bạn đã sẵn sàng",
        "Chạm để xem thông tin dinh dưỡng.",
    )
    assert (failed.title, failed.body) == (
        "Chưa đọc được bữa ăn của bạn",
        "Chạm để xem chi tiết.",
    )


def test_unsupported_language_falls_back_to_english():
    message = build_ai_job_push_message(
        _failed_claim(job_type=AiJobType.CHAT, language="fr")
    )

    assert (message.title, message.body) == (
        "Your coach couldn't finish replying",
        "Tap to try again.",
    )


@pytest.mark.parametrize(
    "claim",
    [make_notification_claim(), _failed_claim()],
    ids=["completed", "failed"],
)
def test_every_data_value_is_a_string(claim):
    # FCM rejects data payloads with non-string values.
    message = build_ai_job_push_message(claim)

    assert all(isinstance(value, str) for value in message.data.values())


# --- run_once ---


@pytest.mark.asyncio
async def test_run_once_claims_with_the_notify_limits(uow, sender, metrics):
    claimed = await _notifier(uow, sender).run_once()

    assert claimed == 0
    uow.ai_jobs.claim_notifications.assert_awaited_once_with(
        limit=20, max_attempts=3, stale_after_seconds=3600
    )
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_once_claims_at_most_one_batch(uow, sender, metrics):
    await _notifier(uow, sender, batch_size=5).run_once()

    assert uow.ai_jobs.claim_notifications.await_args.kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_run_once_pushes_each_claim_outside_the_claim_transaction(
    uow, sender, metrics
):
    uow.ai_jobs.claim_notifications.return_value = [
        make_notification_claim(job_id="job-1"),
        make_notification_claim(job_id="job-2", job_type=AiJobType.CHAT, result=None),
    ]
    transactions_at_send = []

    async def send(targets, message):
        transactions_at_send.append((uow.opened, uow.committed))
        return PushSendReport(sent=1)

    sender.send.side_effect = send

    claimed = await _notifier(uow, sender).run_once()

    assert claimed == 2
    assert [call.args[1].data["job_id"] for call in sender.send.await_args_list] == [
        "job-1",
        "job-2",
    ]
    # The claim and each device lookup commit before the push goes out.
    assert transactions_at_send == [(2, 2), (3, 3)]
    assert _outcomes(metrics) == ["sent", "sent"]


@pytest.mark.asyncio
async def test_one_failed_push_does_not_stop_the_batch(uow, sender, metrics, caplog):
    uow.ai_jobs.claim_notifications.return_value = [
        make_notification_claim(job_id="job-1"),
        make_notification_claim(job_id="job-2"),
    ]
    sender.send.side_effect = [RuntimeError("fcm down"), PushSendReport(sent=1)]

    with caplog.at_level(logging.ERROR, logger=NOTIFIER_LOGGER):
        claimed = await _notifier(uow, sender).run_once()

    assert claimed == 2
    assert sender.send.await_count == 2
    assert _outcomes(metrics) == ["sent"]
    [record] = [r for r in caplog.records if r.name == NOTIFIER_LOGGER]
    assert record.getMessage() == "AI job push failed"
    assert (record.job_id, record.job_type) == ("job-1", "meal_scan")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "break_delivery",
    ["send", "device_lookup"],
    ids=["send-raises", "device-lookup-raises"],
)
async def test_run_once_releases_a_notification_when_delivery_fails(
    uow, sender, metrics, break_delivery
):
    """A claim is committed before the push. An error before it is sent must
    clear that claim, or the notification is never retried."""
    uow.ai_jobs.claim_notifications.return_value = [make_notification_claim()]
    if break_delivery == "send":
        sender.send.side_effect = RuntimeError("fcm down")
    else:
        uow.push_tokens.list_active_targets.side_effect = RuntimeError("db down")

    claimed = await _notifier(uow, sender).run_once()

    assert claimed == 1
    uow.ai_jobs.release_notification.assert_awaited_once_with(
        job_id=JOB_ID, retry_delay_seconds=30.0
    )


@pytest.mark.asyncio
async def test_run_once_keeps_the_claim_when_the_push_already_went_out(
    uow, sender, metrics
):
    uow.ai_jobs.claim_notifications.return_value = [make_notification_claim()]
    sender.send.return_value = PushSendReport(sent=1, invalid_tokens=("android-token",))
    uow.push_tokens.deactivate_tokens.side_effect = RuntimeError("db down")

    claimed = await _notifier(uow, sender).run_once()

    assert claimed == 1
    sender.send.assert_awaited_once()
    uow.ai_jobs.release_notification.assert_not_awaited()


# --- deliver ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [AiJobStatus.QUEUED, AiJobStatus.RUNNING, AiJobStatus.CANCELLED]
)
async def test_deliver_skips_jobs_with_nothing_to_announce(
    uow, sender, metrics, status
):
    await _notifier(uow, sender).deliver(make_notification_claim(status=status))

    assert uow.opened == 0
    sender.send.assert_not_awaited()
    metrics.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_without_a_device_counts_no_device(uow, sender, metrics):
    uow.push_tokens.list_active_targets.return_value = []

    await _notifier(uow, sender).deliver(make_notification_claim())

    uow.push_tokens.list_active_targets.assert_awaited_once_with(user_id=USER_ID)
    sender.send.assert_not_awaited()
    metrics.assert_called_once_with(
        "ai_jobs.push",
        attributes={
            "job_type": "meal_scan",
            "status": "completed",
            "outcome": "no_device",
        },
    )


@pytest.mark.asyncio
async def test_deliver_sends_the_job_message_to_every_device(uow, sender, metrics):
    claim = make_notification_claim()

    await _notifier(uow, sender).deliver(claim)

    uow.push_tokens.list_active_targets.assert_awaited_once_with(user_id=USER_ID)
    sender.send.assert_awaited_once_with(TARGETS, build_ai_job_push_message(claim))
    uow.push_tokens.deactivate_tokens.assert_not_awaited()
    uow.ai_jobs.release_notification.assert_not_awaited()
    metrics.assert_called_once_with(
        "ai_jobs.push",
        attributes={"job_type": "meal_scan", "status": "completed", "outcome": "sent"},
    )


@pytest.mark.asyncio
async def test_deliver_deactivates_dead_tokens_and_still_counts_the_push(
    uow, sender, metrics
):
    sender.send.return_value = PushSendReport(
        sent=1, failed=1, invalid_tokens=("android-token",)
    )

    await _notifier(uow, sender).deliver(make_notification_claim())

    uow.push_tokens.deactivate_tokens.assert_awaited_once_with(("android-token",))
    uow.ai_jobs.release_notification.assert_not_awaited()
    assert _outcomes(metrics) == ["sent"]


@pytest.mark.asyncio
async def test_deliver_to_dead_tokens_only_fails_without_a_retry(uow, sender, metrics):
    sender.send.return_value = PushSendReport(
        failed=2, invalid_tokens=("ios-token", "android-token")
    )

    await _notifier(uow, sender).deliver(make_notification_claim())

    uow.push_tokens.deactivate_tokens.assert_awaited_once_with(
        ("ios-token", "android-token")
    )
    uow.ai_jobs.release_notification.assert_not_awaited()
    assert _outcomes(metrics) == ["failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempts", "delay"), [(1, 30.0), (2, 60.0)])
async def test_deliver_releases_a_temporary_failure_with_a_growing_delay(
    uow, sender, metrics, attempts, delay
):
    sender.send.return_value = PushSendReport(failed=2, retry_later=True)

    await _notifier(uow, sender).deliver(
        make_notification_claim(notify_attempts=attempts)
    )

    uow.ai_jobs.release_notification.assert_awaited_once_with(
        job_id=JOB_ID, retry_delay_seconds=delay
    )
    assert _outcomes(metrics) == ["retry_later"]


@pytest.mark.asyncio
async def test_deliver_deactivates_dead_tokens_before_releasing_a_retry(
    uow, sender, metrics
):
    sender.send.return_value = PushSendReport(
        failed=2, invalid_tokens=("android-token",), retry_later=True
    )

    await _notifier(uow, sender).deliver(make_notification_claim())

    uow.push_tokens.deactivate_tokens.assert_awaited_once_with(("android-token",))
    uow.ai_jobs.release_notification.assert_awaited_once_with(
        job_id=JOB_ID, retry_delay_seconds=30.0
    )
    # Device lookup, deactivation and release each commit on their own.
    assert (uow.opened, uow.committed) == (3, 3)
    assert _outcomes(metrics) == ["retry_later"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [3, 4])
async def test_deliver_gives_up_after_the_last_notify_attempt(
    uow, sender, metrics, attempts
):
    sender.send.return_value = PushSendReport(failed=2, retry_later=True)

    await _notifier(uow, sender).deliver(
        make_notification_claim(notify_attempts=attempts)
    )

    uow.ai_jobs.release_notification.assert_not_awaited()
    assert _outcomes(metrics) == ["failed"]


@pytest.mark.asyncio
async def test_deliver_counts_a_permanent_rejection_as_failed(uow, sender, metrics):
    sender.send.return_value = PushSendReport(failed=2)

    await _notifier(uow, sender).deliver(
        _failed_claim(job_type=AiJobType.CHAT, error_code="CHAT_TURN_FAILED")
    )

    uow.ai_jobs.release_notification.assert_not_awaited()
    metrics.assert_called_once_with(
        "ai_jobs.push",
        attributes={"job_type": "chat", "status": "failed", "outcome": "failed"},
    )


# --- run ---


@pytest.mark.asyncio
async def test_run_returns_at_once_when_already_stopped(uow, sender, metrics):
    probe = StubSchemaProbe()
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(_notifier(uow, sender, ready=probe.ready).run(stop), 1)

    assert probe.calls == 0
    uow.ai_jobs.claim_notifications.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_idles_while_the_tables_are_not_ready(uow, sender, metrics):
    stop = asyncio.Event()
    probe = StubSchemaProbe(ready=False)

    async def ready() -> bool:
        answer = await probe.ready()
        if probe.calls == 3:
            stop.set()
        return answer

    notifier = _notifier(uow, sender, ready=ready, interval_seconds=0.001)

    await asyncio.wait_for(notifier.run(stop), 1)

    assert probe.calls == 3
    assert uow.opened == 0
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_without_a_probe_claims_on_every_pass(uow, sender, metrics):
    stop = asyncio.Event()
    passes = []

    async def claim(**kwargs):
        passes.append(kwargs)
        if len(passes) == 2:
            stop.set()
        return []

    uow.ai_jobs.claim_notifications.side_effect = claim

    await asyncio.wait_for(_notifier(uow, sender, interval_seconds=0.001).run(stop), 1)

    assert len(passes) == 2


@pytest.mark.asyncio
async def test_run_survives_a_failed_pass(uow, sender, metrics, caplog):
    stop = asyncio.Event()
    passes = []

    async def claim(**kwargs):
        passes.append(kwargs)
        if len(passes) == 1:
            raise RuntimeError("db down")
        stop.set()
        return []

    uow.ai_jobs.claim_notifications.side_effect = claim
    notifier = _notifier(
        uow, sender, ready=StubSchemaProbe().ready, interval_seconds=0.001
    )

    with caplog.at_level(logging.ERROR, logger=NOTIFIER_LOGGER):
        await asyncio.wait_for(notifier.run(stop), 1)

    assert len(passes) == 2
    assert "AI job notifier pass failed" in caplog.messages


@pytest.mark.asyncio
async def test_run_goes_straight_to_the_next_batch_when_one_is_full(
    uow, sender, metrics
):
    stop = asyncio.Event()
    batches = [
        [
            make_notification_claim(job_id="job-1"),
            make_notification_claim(job_id="job-2"),
        ],
        [make_notification_claim(job_id="job-3")],
    ]

    async def claim(**kwargs):
        batch = batches.pop(0)
        if not batches:
            stop.set()
        return batch

    uow.ai_jobs.claim_notifications.side_effect = claim
    # A 30 s wait between the batches would trip the 1 s timeout.
    notifier = _notifier(uow, sender, batch_size=2, interval_seconds=30)

    await asyncio.wait_for(notifier.run(stop), 1)

    assert uow.ai_jobs.claim_notifications.await_count == 2
    assert sender.send.await_count == 3


@pytest.mark.asyncio
async def test_run_wakes_from_its_wait_when_stopped(uow, sender, metrics):
    stop = asyncio.Event()
    claimed = asyncio.Event()

    async def claim(**kwargs):
        claimed.set()
        return []

    uow.ai_jobs.claim_notifications.side_effect = claim
    task = asyncio.create_task(_notifier(uow, sender, interval_seconds=30).run(stop))
    await asyncio.wait_for(claimed.wait(), 1)

    stop.set()
    await asyncio.wait_for(task, 1)

    assert uow.ai_jobs.claim_notifications.await_count == 1
