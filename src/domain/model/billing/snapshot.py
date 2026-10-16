"""Provider-neutral view of one billing customer, as fetched from the provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.domain.model.billing.constants import ENTITLEMENT_STANDARD


@dataclass(frozen=True)
class NormalizedSubscription:
    """One store subscription (or entitlement-backed purchase) in a snapshot.

    ``subscription_key`` is derived only from the fetched snapshot, so every
    sync path (webhook, ``/sync``, reconciler, backfill) computes the same key.
    ``state`` is evaluated at the snapshot's ``fetched_at``; access is always
    re-evaluated from the dates at read time.
    """

    subscription_key: str
    store: str
    environment: str
    source_customer_id: str
    product_id: str
    # From the provider's own entitlement mapping; None = grants nothing.
    entitlement_key: str | None
    state: str
    period_type: str | None
    will_renew: bool | None
    is_lifetime: bool
    purchased_at: datetime | None
    expires_at: datetime | None
    grace_expires_at: datetime | None
    unsubscribe_detected_at: datetime | None
    billing_issue_detected_at: datetime | None
    refunded_at: datetime | None
    store_identifier: str | None = None
    original_transaction_id: str | None = None
    ownership_type: str | None = None


@dataclass(frozen=True)
class ProviderEntitlement:
    """The provider's own entitlement verdict, kept for shadow comparison."""

    entitlement_key: str
    product_id: str | None
    purchased_at: datetime | None
    expires_at: datetime | None
    grace_expires_at: datetime | None

    def is_active_at(self, now: datetime) -> bool:
        """Mirror the RevenueCat SDKs: active while ``expires_date`` is null or future."""
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True)
class CustomerSnapshot:
    """Everything one provider fetch says about one customer."""

    provider: str
    canonical_customer_id: str
    aliases: list[str]
    subscriptions: list[NormalizedSubscription]
    fetched_at: datetime
    # The id we asked the provider for; may differ from the canonical id.
    requested_customer_id: str | None = None
    entitlements: dict[str, ProviderEntitlement] = field(default_factory=dict)

    def provider_reports_active(
        self, entitlement_key: str = ENTITLEMENT_STANDARD, *, now: datetime
    ) -> bool:
        """Whether the provider itself calls ``entitlement_key`` active at ``now``."""
        entitlement = self.entitlements.get(entitlement_key)
        return entitlement is not None and entitlement.is_active_at(now)

    def customer_ids(self) -> list[str]:
        """Canonical id first, then aliases, without duplicates."""
        ids = [self.canonical_customer_id]
        for alias in self.aliases:
            if alias not in ids:
                ids.append(alias)
        return ids
