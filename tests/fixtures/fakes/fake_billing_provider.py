"""Fakes and builders for entitlement sync tests (NM-680)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.domain.exceptions.billing_exceptions import ProviderNotFound
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    PROVIDER_REVENUECAT,
)
from src.domain.model.billing.snapshot import (
    CustomerSnapshot,
    NormalizedSubscription,
    ProviderEntitlement,
)
from src.domain.model.user import UserDomainModel
from src.domain.ports.billing_provider_budget_port import BillingProviderBudgetPort
from src.domain.ports.billing_provider_port import BillingProviderPort

# Fixed test time. Use it only with services that get a ``FakeClock(NOW)``; code
# on the wall clock needs dates relative to ``datetime.now(UTC)`` instead (CI
# re-runs these suites with the clock moved forward: tests/fixtures/clock_travel.py).
NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now = self.now + timedelta(**delta)
        return self.now


class FakeBillingProvider(BillingProviderPort):
    """Answers from ``responses``; an Exception value is raised. Unknown ids 404."""

    provider = PROVIDER_REVENUECAT

    def __init__(self) -> None:
        self.responses: dict[str, CustomerSnapshot | Exception] = {}
        self.calls: list[str] = []
        # Called before every fetch, e.g. to assert no transaction is open.
        self.on_fetch: Callable[[str], None] | None = None

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        self.calls.append(customer_id)
        if self.on_fetch is not None:
            self.on_fetch(customer_id)
        response = self.responses.get(customer_id)
        if response is None:
            raise ProviderNotFound("unknown customer", provider=self.provider)
        if isinstance(response, Exception):
            raise response
        return response


class FakeBudget(BillingProviderBudgetPort):
    def __init__(self) -> None:
        self.lanes: list[str] = []

    @asynccontextmanager
    async def acquire(
        self, lane: str, timeout: float | None = None
    ) -> AsyncIterator[None]:
        self.lanes.append(str(lane))
        yield


def make_user(user_id: str, firebase_uid: str, *, active: bool = True):
    return UserDomainModel(
        id=user_id,  # type: ignore[arg-type]
        firebase_uid=firebase_uid,
        email=f"{user_id}@example.test",
        username=user_id,
        password_hash="",
        provider=AuthProvider.GOOGLE,
        is_active=active,
    )


def make_sub(
    customer: str,
    product: str = "nutree_monthly",
    *,
    state: str = "active",
    expires_at: datetime | None = NOW + timedelta(days=30),
    grace_expires_at: datetime | None = None,
    environment: str = "production",
    entitlement_key: str | None = ENTITLEMENT_STANDARD,
    will_renew: bool | None = True,
    period_type: str | None = "normal",
    store: str = "app_store",
    is_lifetime: bool = False,
    billing_issue_detected_at: datetime | None = None,
) -> NormalizedSubscription:
    return NormalizedSubscription(
        subscription_key=f"rc:{customer}:{product}",
        store=store,
        environment=environment,
        source_customer_id=customer,
        product_id=product,
        entitlement_key=entitlement_key,
        state=state,
        period_type=period_type,
        will_renew=will_renew,
        is_lifetime=is_lifetime,
        purchased_at=NOW - timedelta(days=1),
        expires_at=expires_at,
        grace_expires_at=grace_expires_at,
        unsubscribe_detected_at=None,
        billing_issue_detected_at=billing_issue_detected_at,
        refunded_at=None,
    )


@dataclass
class SnapshotBuilder:
    customer: str
    fetched_at: datetime = NOW
    requested: str | None = None
    subscriptions: list[NormalizedSubscription] = field(default_factory=list)
    rc_active: bool | None = None

    def build(self) -> CustomerSnapshot:
        requested = self.requested or self.customer
        active = (
            self.rc_active
            if self.rc_active is not None
            else any(
                sub.entitlement_key == ENTITLEMENT_STANDARD
                and (sub.expires_at is None or sub.expires_at > self.fetched_at)
                for sub in self.subscriptions
            )
        )
        entitlements = (
            {
                ENTITLEMENT_STANDARD: ProviderEntitlement(
                    entitlement_key=ENTITLEMENT_STANDARD,
                    product_id=None,
                    purchased_at=None,
                    expires_at=None,
                    grace_expires_at=None,
                )
            }
            if active
            else {}
        )
        return CustomerSnapshot(
            provider=PROVIDER_REVENUECAT,
            canonical_customer_id=self.customer,
            aliases=[requested] if requested != self.customer else [],
            subscriptions=list(self.subscriptions),
            fetched_at=self.fetched_at,
            requested_customer_id=requested,
            entitlements=entitlements,
        )


def snapshot(
    customer: str,
    *subscriptions: NormalizedSubscription,
    fetched_at: datetime = NOW,
    requested: str | None = None,
    rc_active: bool | None = None,
) -> CustomerSnapshot:
    return SnapshotBuilder(
        customer=customer,
        fetched_at=fetched_at,
        requested=requested,
        subscriptions=list(subscriptions),
        rc_active=rc_active,
    ).build()
