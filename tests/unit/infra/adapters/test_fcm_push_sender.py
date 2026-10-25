"""FcmPushSender sends one push per device through firebase_admin."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging

from src.domain.model.notification import (
    DeviceType,
    PushMessage,
    PushSendReport,
    PushTarget,
)
from src.infra.adapters import fcm_push_sender
from src.infra.adapters.fcm_push_sender import FcmPushSender

SENDER_LOGGER = fcm_push_sender.__name__
MESSAGE = PushMessage(
    title="Your meal is ready",
    body="Tap to see the nutrition breakdown.",
    data={
        "type": "ai_job",
        "job_id": "job-1",
        "job_type": "meal_scan",
        "status": "completed",
        "meal_id": "meal-1",
    },
    thread_id="ai_job",
    collapse_key="job-1",
)
# time.time() during the iOS tests; APNs expiry is an hour later.
FAKE_NOW = 1_000_000.0


def _ios(token: str) -> PushTarget:
    return PushTarget(token=token, device_type=DeviceType.IOS)


def _android(token: str) -> PushTarget:
    return PushTarget(token=token, device_type=DeviceType.ANDROID)


class FakeSendEach:
    """Stands in for ``messaging.send_each``; fails the tokens it is told to."""

    def __init__(self, errors=None, *, batch_errors=None):
        self._errors = errors or {}
        self._batch_errors = list(batch_errors or [])
        self.batches: list[list[messaging.Message]] = []
        self.threads: list[str] = []

    def __call__(self, messages):
        self.batches.append(list(messages))
        self.threads.append(threading.current_thread().name)
        if self._batch_errors:
            error = self._batch_errors.pop(0)
            if error is not None:
                raise error
        return messaging.BatchResponse(
            [
                messaging.SendResponse(None, self._errors[message.token])
                if message.token in self._errors
                else messaging.SendResponse(
                    {"name": f"projects/p/messages/{index}"}, None
                )
                for index, message in enumerate(messages)
            ]
        )

    @property
    def tokens(self) -> list[list[str]]:
        return [[message.token for message in batch] for batch in self.batches]


@pytest.fixture
def executor():
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fcm-test")
    yield pool
    pool.shutdown(wait=True)


@pytest.fixture
def firebase_ready(monkeypatch):
    monkeypatch.setattr(fcm_push_sender, "_firebase_ready", lambda: True)


async def _send(executor, targets, message=MESSAGE, **fake_kwargs):
    fake = FakeSendEach(**fake_kwargs)
    report = await FcmPushSender(executor=executor, send_each=fake).send(
        targets, message
    )
    return report, fake


def _logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == SENDER_LOGGER]


# --- readiness ---


@pytest.mark.asyncio
async def test_no_targets_sends_nothing(executor):
    report, fake = await _send(executor, [])

    assert report == PushSendReport()
    assert fake.batches == []


@pytest.mark.asyncio
async def test_uninitialised_firebase_fails_every_unique_device(
    executor, monkeypatch, caplog
):
    monkeypatch.setattr(fcm_push_sender, "_firebase_ready", lambda: False)

    with caplog.at_level(logging.WARNING, logger=SENDER_LOGGER):
        report, fake = await _send(executor, [_ios("a"), _ios("a"), _android("b")])

    assert report == PushSendReport(failed=2)
    assert fake.batches == []
    assert _logged(caplog) == ["Firebase is not initialized; push skipped"]


def test_firebase_ready_needs_a_default_app():
    with patch.object(
        fcm_push_sender.firebase_admin, "get_app", side_effect=ValueError("no app")
    ):
        assert fcm_push_sender._firebase_ready() is False
    with patch.object(fcm_push_sender.firebase_admin, "get_app", return_value=object()):
        assert fcm_push_sender._firebase_ready() is True


def test_default_send_each_is_firebase_admin(executor):
    assert FcmPushSender(executor=executor)._send_each is messaging.send_each


# --- batching ---


@pytest.mark.asyncio
async def test_each_token_gets_one_message_off_the_event_loop(executor, firebase_ready):
    report, fake = await _send(executor, [_ios("a"), _android("b"), _ios("a")])

    assert report == PushSendReport(sent=2)
    assert fake.tokens == [["a", "b"]]
    assert fake.threads[0].startswith("fcm-test")
    ios_message, android_message = fake.batches[0]
    assert ios_message.apns is not None and ios_message.android is None
    assert android_message.android is not None and android_message.apns is None


@pytest.mark.asyncio
async def test_sends_at_most_500_messages_per_call(executor, firebase_ready):
    targets = [_android(f"t{i}") for i in range(1001)]

    report, fake = await _send(executor, targets)

    assert [len(batch) for batch in fake.batches] == [500, 500, 1]
    assert [token for batch in fake.tokens for token in batch] == [
        f"t{i}" for i in range(1001)
    ]
    assert report == PushSendReport(sent=1001)


# --- per-device results ---


@pytest.mark.asyncio
async def test_classifies_each_rejected_device(executor, firebase_ready, caplog):
    errors = {
        "unregistered": messaging.UnregisteredError("Requested entity was not found."),
        "mismatch": messaging.SenderIdMismatchError("SenderId mismatch"),
        "bad-token": firebase_exceptions.InvalidArgumentError(
            "The registration token is not a valid FCM registration token"
        ),
        "bad-payload": firebase_exceptions.InvalidArgumentError(
            "Invalid JSON payload received."
        ),
        "auth": messaging.ThirdPartyAuthError("Auth error from APNS"),
    }
    targets = [
        _android("unregistered"),
        _ios("mismatch"),
        _android("bad-token"),
        _ios("bad-payload"),
        _ios("auth"),
        _android("ok"),
    ]

    with caplog.at_level(logging.WARNING, logger=SENDER_LOGGER):
        report, _ = await _send(executor, targets, errors=errors)

    assert report == PushSendReport(
        sent=1,
        failed=5,
        invalid_tokens=("unregistered", "mismatch", "bad-token"),
        retry_later=False,
    )
    assert _logged(caplog) == [
        "FCM rejected a ios push: InvalidArgumentError",
        "FCM rejected a ios push: ThirdPartyAuthError",
    ]


@pytest.mark.parametrize(
    ("error", "invalid"),
    [
        (messaging.UnregisteredError("gone"), True),
        (messaging.SenderIdMismatchError("mismatch"), True),
        (
            firebase_exceptions.InvalidArgumentError(
                "The REGISTRATION TOKEN is malformed"
            ),
            True,
        ),
        (
            firebase_exceptions.InvalidArgumentError("Invalid JSON payload received."),
            False,
        ),
        (firebase_exceptions.NotFoundError("Requested entity was not found."), False),
        (ValueError("registration token"), False),
        (None, False),
    ],
    ids=[
        "unregistered",
        "sender-mismatch",
        "invalid-token",
        "invalid-payload",
        "bare-not-found",
        "not-a-firebase-error",
        "no-error",
    ],
)
def test_only_dead_tokens_are_invalid(error, invalid):
    # A malformed payload must never deactivate a healthy device.
    assert fcm_push_sender._is_invalid_token(error) is invalid


TEMPORARY_ERRORS = [
    firebase_exceptions.UnavailableError,
    firebase_exceptions.InternalError,
    firebase_exceptions.DeadlineExceededError,
    firebase_exceptions.ResourceExhaustedError,
    messaging.QuotaExceededError,
    firebase_exceptions.UnknownError,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", TEMPORARY_ERRORS, ids=lambda t: t.__name__)
async def test_temporary_failures_everywhere_ask_for_a_retry(
    executor, firebase_ready, error_type
):
    errors = {"a": error_type("try later"), "b": error_type("try later")}

    report, _ = await _send(executor, [_ios("a"), _android("b")], errors=errors)

    assert report == PushSendReport(failed=2, retry_later=True)


@pytest.mark.asyncio
async def test_no_retry_once_any_device_got_the_push(executor, firebase_ready):
    errors = {"a": firebase_exceptions.UnavailableError("try later")}

    report, _ = await _send(executor, [_ios("a"), _android("b")], errors=errors)

    assert report == PushSendReport(sent=1, failed=1)


@pytest.mark.asyncio
async def test_dead_token_and_temporary_failure_report_both(executor, firebase_ready):
    errors = {
        "gone": messaging.UnregisteredError("Requested entity was not found."),
        "busy": firebase_exceptions.UnavailableError("try later"),
    }

    report, _ = await _send(executor, [_ios("gone"), _android("busy")], errors=errors)

    assert report == PushSendReport(
        failed=2, invalid_tokens=("gone",), retry_later=True
    )


# --- whole-batch failures ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "retry_later"),
    [
        (firebase_exceptions.UnavailableError("FCM is down"), True),
        (firebase_exceptions.UnknownError("connection reset"), True),
        (firebase_exceptions.UnauthenticatedError("bad credentials"), False),
        (ValueError("invalid message"), False),
    ],
    ids=["unavailable", "transport", "unauthenticated", "not-a-firebase-error"],
)
async def test_failed_batch_call_fails_every_device_in_it(
    executor, firebase_ready, caplog, error, retry_later
):
    with caplog.at_level(logging.WARNING, logger=SENDER_LOGGER):
        report, _ = await _send(
            executor, [_ios("a"), _android("b")], batch_errors=[error]
        )

    assert report == PushSendReport(failed=2, retry_later=retry_later)
    assert _logged(caplog) == [f"FCM batch send failed: {type(error).__name__}"]


@pytest.mark.asyncio
async def test_failed_batch_does_not_stop_the_next_one(executor, firebase_ready):
    targets = [_android(f"t{i}") for i in range(501)]

    report, fake = await _send(
        executor,
        targets,
        batch_errors=[firebase_exceptions.UnavailableError("FCM is down"), None],
    )

    assert [len(batch) for batch in fake.batches] == [500, 1]
    assert report == PushSendReport(sent=1, failed=500)


# --- message shape ---


@pytest.mark.asyncio
async def test_ios_gets_a_native_alert_that_expires_in_an_hour(
    executor, firebase_ready
):
    with patch("src.infra.adapters.fcm_push_sender.time") as clock:
        clock.time.return_value = FAKE_NOW
        _, fake = await _send(executor, [_ios("a")])

    [[built]] = fake.batches
    assert built.token == "a"
    assert built.apns.headers == {
        "apns-priority": "10",
        "apns-push-type": "alert",
        "apns-expiration": "1003600",
        "apns-collapse-id": "job-1",
    }
    aps = built.apns.payload.aps
    assert (aps.alert.title, aps.alert.body) == (MESSAGE.title, MESSAGE.body)
    assert aps.sound == "default"
    assert aps.thread_id == "ai_job"
    assert built.android is None
    assert built.notification is None
    assert built.data == {**MESSAGE.data, "title": MESSAGE.title, "body": MESSAGE.body}


@pytest.mark.asyncio
async def test_ios_without_a_collapse_key_or_thread(executor, firebase_ready):
    _, fake = await _send(executor, [_ios("a")], PushMessage(title="T", body="B"))

    [[built]] = fake.batches
    assert "apns-collapse-id" not in built.apns.headers
    assert built.apns.payload.aps.thread_id is None
    assert built.data == {"title": "T", "body": "B"}


@pytest.mark.asyncio
async def test_android_gets_a_high_priority_data_only_message(executor, firebase_ready):
    _, fake = await _send(executor, [_android("b")])

    [[built]] = fake.batches
    assert built.token == "b"
    assert built.android.priority == "high"
    assert built.android.ttl == timedelta(hours=1)
    assert built.android.collapse_key == "job-1"
    # The app renders Android notifications itself.
    assert built.android.notification is None
    assert built.notification is None
    assert built.apns is None
    assert built.data == {**MESSAGE.data, "title": MESSAGE.title, "body": MESSAGE.body}


@pytest.mark.asyncio
async def test_title_and_body_win_over_same_named_data(executor, firebase_ready):
    message = PushMessage(
        title="T", body="B", data={"title": "stale", "body": "stale", "job_id": "j"}
    )

    _, fake = await _send(executor, [_ios("a"), _android("b")], message)

    for built in fake.batches[0]:
        assert built.data == {"title": "T", "body": "B", "job_id": "j"}


# encode_message runs the validation send_each runs, without a Firebase app.


@pytest.mark.asyncio
async def test_ios_message_passes_firebase_validation(executor, firebase_ready):
    with patch("src.infra.adapters.fcm_push_sender.time") as clock:
        clock.time.return_value = FAKE_NOW
        _, fake = await _send(executor, [_ios("a")])

    encoded = messaging._MessagingService.encode_message(fake.batches[0][0])

    assert encoded["token"] == "a"
    assert encoded["data"] == {
        **MESSAGE.data,
        "title": MESSAGE.title,
        "body": MESSAGE.body,
    }
    assert encoded["apns"]["headers"] == {
        "apns-priority": "10",
        "apns-push-type": "alert",
        "apns-expiration": "1003600",
        "apns-collapse-id": "job-1",
    }
    assert encoded["apns"]["payload"] == {
        "aps": {
            "alert": {"title": MESSAGE.title, "body": MESSAGE.body},
            "sound": "default",
            "thread-id": "ai_job",
        }
    }
    assert "android" not in encoded


@pytest.mark.asyncio
async def test_android_message_passes_firebase_validation(executor, firebase_ready):
    _, fake = await _send(executor, [_android("b")])

    encoded = messaging._MessagingService.encode_message(fake.batches[0][0])

    assert encoded["token"] == "b"
    assert encoded["android"] == {
        "collapse_key": "job-1",
        "priority": "high",
        "ttl": "3600s",
    }
    assert "apns" not in encoded
    assert "notification" not in encoded
