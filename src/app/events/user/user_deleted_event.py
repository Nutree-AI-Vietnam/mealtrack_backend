"""Integration event emitted after a user account is deleted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class UserDeletedEvent(IntegrationEvent):
    """Published after a user account is soft-deleted and anonymized."""

    event_type: Literal[
        "user.deleted.v1",
        "user.account_cleanup.v1",
        "firebase_account_cleanup",
    ] = "user.deleted.v1"
    aggregate_type: Literal["user"] = "user"
