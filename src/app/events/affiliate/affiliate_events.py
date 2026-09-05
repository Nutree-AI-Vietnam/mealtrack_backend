"""Affiliate and subscription lifecycle integration events."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class AffiliateAttributionCreatedEvent(IntegrationEvent):
    """Published when a referral or affiliate code is attributed to a user."""

    event_type: Literal["affiliate.attribution_created.v1"] = (
        "affiliate.attribution_created.v1"
    )
    aggregate_type: Literal["affiliate"] = "affiliate"


class SubscriptionLifecycleEvent(IntegrationEvent):
    """Published when a subscription purchase, renewal, cancellation, or refund occurs."""

    event_type: Literal["subscription.lifecycle.v1"] = "subscription.lifecycle.v1"
    aggregate_type: Literal["subscription"] = "subscription"
