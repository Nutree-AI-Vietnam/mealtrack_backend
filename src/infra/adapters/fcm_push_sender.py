"""Push delivery through Firebase Cloud Messaging.

iOS gets a native APNs alert. Android gets a data-only message that the app
renders itself, so it can pick the channel and skip results already on screen.
Title and body are always mirrored into ``data`` for the app's handlers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from datetime import timedelta

import firebase_admin
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging

from src.domain.model.notification import (
    DeviceType,
    PushMessage,
    PushSendReport,
    PushTarget,
)
from src.domain.ports.push_sender_port import PushSenderPort

logger = logging.getLogger(__name__)

# send_each accepts at most 500 messages per call.
_BATCH_SIZE = 500
# A result announced an hour late is noise; the app shows it on next open.
_TIME_TO_LIVE = timedelta(hours=1)

_TEMPORARY_ERRORS = (
    firebase_exceptions.UnavailableError,
    firebase_exceptions.InternalError,
    firebase_exceptions.DeadlineExceededError,
    # Includes messaging.QuotaExceededError.
    firebase_exceptions.ResourceExhaustedError,
    # Transport failures without a recognised FCM status.
    firebase_exceptions.UnknownError,
)

SendEach = Callable[[list[messaging.Message]], messaging.BatchResponse]


class FcmPushSender(PushSenderPort):
    def __init__(self, *, executor: Executor, send_each: SendEach | None = None):
        self._executor = executor
        self._send_each: SendEach = send_each or messaging.send_each

    async def send(
        self, targets: Sequence[PushTarget], message: PushMessage
    ) -> PushSendReport:
        unique = list({target.token: target for target in targets}.values())
        if not unique:
            return PushSendReport()
        if not _firebase_ready():
            logger.warning("Firebase is not initialized; push skipped")
            return PushSendReport(failed=len(unique))

        loop = asyncio.get_running_loop()
        sent = failed = temporary = 0
        invalid: list[str] = []
        for start in range(0, len(unique), _BATCH_SIZE):
            batch = unique[start : start + _BATCH_SIZE]
            messages = [_build_message(target, message) for target in batch]
            try:
                response = await loop.run_in_executor(
                    self._executor, self._send_each, messages
                )
            except Exception as exc:
                logger.warning("FCM batch send failed: %s", type(exc).__name__)
                failed += len(batch)
                temporary += len(batch) if _is_temporary(exc) else 0
                continue
            for target, result in zip(batch, response.responses, strict=True):
                if result.success:
                    sent += 1
                    continue
                failed += 1
                error = result.exception
                if _is_invalid_token(error):
                    invalid.append(target.token)
                elif _is_temporary(error):
                    temporary += 1
                else:
                    logger.warning(
                        "FCM rejected a %s push: %s",
                        target.device_type,
                        type(error).__name__,
                    )
        return PushSendReport(
            sent=sent,
            failed=failed,
            invalid_tokens=tuple(invalid),
            retry_later=sent == 0 and temporary > 0,
        )


def _firebase_ready() -> bool:
    try:
        firebase_admin.get_app()
    except ValueError:
        return False
    return True


def _build_message(target: PushTarget, message: PushMessage) -> messaging.Message:
    data = {**message.data, "title": message.title, "body": message.body}
    if target.device_type is DeviceType.IOS:
        headers = {
            "apns-priority": "10",
            "apns-push-type": "alert",
            "apns-expiration": str(int(time.time() + _TIME_TO_LIVE.total_seconds())),
        }
        if message.collapse_key:
            headers["apns-collapse-id"] = message.collapse_key
        return messaging.Message(
            token=target.token,
            data=data,
            apns=messaging.APNSConfig(
                headers=headers,
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(
                            title=message.title, body=message.body
                        ),
                        sound="default",
                        thread_id=message.thread_id,
                    )
                ),
            ),
        )
    return messaging.Message(
        token=target.token,
        data=data,
        android=messaging.AndroidConfig(
            priority="high",
            ttl=_TIME_TO_LIVE,
            collapse_key=message.collapse_key,
        ),
    )


def _is_invalid_token(error: BaseException | None) -> bool:
    if isinstance(
        error, (messaging.UnregisteredError, messaging.SenderIdMismatchError)
    ):
        return True
    # INVALID_ARGUMENT also covers malformed payloads, which must never
    # deactivate a healthy device.
    return isinstance(error, firebase_exceptions.InvalidArgumentError) and (
        "registration token" in str(error).lower()
    )


def _is_temporary(error: BaseException | None) -> bool:
    return isinstance(error, _TEMPORARY_ERRORS)
