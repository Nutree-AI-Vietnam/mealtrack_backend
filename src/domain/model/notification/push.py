"""Value objects for sending push notifications to devices."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.model.notification.enums import DeviceType


@dataclass(frozen=True, slots=True)
class PushTarget:
    token: str
    device_type: DeviceType


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    # FCM data values must be strings.
    data: dict[str, str] = field(default_factory=dict)
    # Groups notifications on iOS (thread-id).
    thread_id: str | None = None
    # A newer message with the same key replaces an undelivered older one.
    collapse_key: str | None = None


@dataclass(frozen=True, slots=True)
class PushSendReport:
    sent: int = 0
    failed: int = 0
    # Tokens the provider says will never work again; callers deactivate them.
    invalid_tokens: tuple[str, ...] = ()
    # True when nothing was delivered but some device failed only temporarily,
    # so sending again later may reach it.
    retry_later: bool = False
