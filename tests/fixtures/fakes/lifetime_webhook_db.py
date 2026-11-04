"""A real SQLite subscriptions table behind the webhook's unit of work.

Webhook handlers run against the real repository, so each check on rows also
runs the real ``has_entitled_subscription`` query the Premium gate asks
first. The session never autoflushes, as in production, and every delivery
commits, as each webhook's unit of work does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.domain.constants.lifetime_products import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
)
from src.domain.ports.engagement_push_repository_port import (
    EngagementPushRepositoryPort,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.subscription import Subscription
from src.infra.repositories.subscription_repository_async import (
    AsyncSubscriptionRepository,
)

__all__ = [
    "LIFETIME_PRODUCT_ID",
    "LIFETIME_UPGRADE_PRODUCT_ID",
    "MONTHLY",
    "SUBSCRIBER",
    "USER",
    "YEARLY",
    "commit",
    "entitled",
    "event",
    "lifecycle_types",
    "lifetime",
    "ms",
    "open_subscription_uow",
    "rows",
    "yearly",
]

YEARLY = "discount_nutree_annually_0"
MONTHLY = "nutree_monthly_99k"
SUBSCRIBER = "firebase-uid-1"
USER = SimpleNamespace(
    id="user-1",
    firebase_uid=SUBSCRIBER,
    timezone="Asia/Ho_Chi_Minh",
    language_code="vi",
)


class SqliteAsyncSession:
    """The AsyncSession calls the webhook makes, over a sync SQLite session.

    Savepoints are a no-op: the push outbox they guard is a spy here.
    """

    def __init__(self, session: Any) -> None:
        self.sync = session

    def add(self, row: Any) -> None:
        self.sync.add(row)

    def get_bind(self) -> Any:
        return self.sync.get_bind()

    async def flush(self) -> None:
        self.sync.flush()

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return self.sync.execute(statement, params)

    @asynccontextmanager
    async def begin_nested(self):
        yield


class Referrals:
    """One referral conversion with the wallet arithmetic of ReferralRepository."""

    def __init__(self) -> None:
        self.conversion = SimpleNamespace(
            status="pending",
            converted_at=None,
            revoked_at=None,
            referrer_user_id="referrer-1",
            commission_amount=50_000,
            commission_amount_vnd=50_000,
            commission_currency="VND",
        )
        self.balance = 0

    async def get_conversion_by_referred_user(self, user_id, for_update=False):
        return self.conversion

    async def credit_wallet(self, user_id, amount):
        self.balance += amount

    async def revoke_from_wallet(self, user_id, amount):
        self.balance -= amount


@contextmanager
def open_subscription_uow() -> Iterator[SimpleNamespace]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    async_session = SqliteAsyncSession(session)
    pushes = create_autospec(EngagementPushRepositoryPort, instance=True)
    pushes.enqueue.return_value = True
    try:
        yield SimpleNamespace(
            session=async_session,
            subscriptions=AsyncSubscriptionRepository(async_session),
            referrals=Referrals(),
            engagement_pushes=pushes,
        )
    finally:
        session.close()
        engine.dispose()


def commit(uow: SimpleNamespace) -> None:
    """End the webhook's unit of work."""
    uow.session.sync.commit()


def ms(days: int) -> int:
    return int((utc_now() + timedelta(days=days)).timestamp() * 1000)


def event(event_type: str, product: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": f"evt-{event_type}-{product}",
        "type": event_type,
        "app_user_id": SUBSCRIBER,
        "product_id": product,
        "store": "APP_STORE",
        "environment": "PRODUCTION",
        "period_type": "NORMAL",
        "purchased_at_ms": ms(0),
        "transaction_id": f"tx-{product}",
        **fields,
    }


def lifetime(product: str = LIFETIME_PRODUCT_ID, **fields: Any) -> dict[str, Any]:
    fields.setdefault("expiration_at_ms", None)
    return event("NON_RENEWING_PURCHASE", product, **fields)


def yearly(event_type: str, **fields: Any) -> dict[str, Any]:
    fields.setdefault("expiration_at_ms", ms(365))
    return event(event_type, YEARLY, **fields)


async def rows(uow: SimpleNamespace) -> list[Subscription]:
    result = await uow.session.execute(
        select(Subscription).order_by(Subscription.purchased_at, Subscription.id)
    )
    return list(result.scalars().all())


async def entitled(
    uow: SimpleNamespace, user_id: str = USER.id, *, exclude_sandbox: bool = True
) -> bool:
    return await uow.subscriptions.has_entitled_subscription(
        user_id, exclude_sandbox=exclude_sandbox
    )


def lifecycle_types(publisher: Any) -> list[str]:
    return [
        call.args[0]["data"]["lifecycle_type"]
        for call in publisher.publish.await_args_list
    ]
