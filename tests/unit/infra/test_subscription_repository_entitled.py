"""The entitled-subscription query keeps paid-up rows and drops expired or sandbox ones."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.domain.utils.timezone_utils import utc_now
from src.infra.repositories.subscription_repository_async import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    AsyncSubscriptionRepository,
)


async def _compiled_query(*, exclude_sandbox: bool):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result)
    await AsyncSubscriptionRepository(session).has_entitled_subscription(
        "user-1", exclude_sandbox=exclude_sandbox
    )
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    where = str(compiled).split("WHERE", 1)[1]
    return where, compiled.params


@pytest.mark.asyncio
async def test_only_active_and_billing_issue_rows_count_until_expiry():
    """Cancelled rows go to RevenueCat: refunds arrive as CANCELLATION too."""
    where, params = await _compiled_query(exclude_sandbox=False)

    assert ENTITLED_SUBSCRIPTION_STATUSES == ("active", "billing_issue")
    assert "subscriptions.status IN" in where
    assert list(ENTITLED_SUBSCRIPTION_STATUSES) in params.values()


@pytest.mark.asyncio
async def test_expired_rows_do_not_count():
    before = utc_now()
    where, params = await _compiled_query(exclude_sandbox=False)

    assert "subscriptions.expires_at > %(expires_at_1)s" in where
    assert before <= params["expires_at_1"] <= utc_now() + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_sandbox_rows_are_dropped_only_when_asked():
    production_where, _ = await _compiled_query(exclude_sandbox=True)
    other_where, _ = await _compiled_query(exclude_sandbox=False)

    assert "subscriptions.is_sandbox IS false" in production_where
    assert "is_sandbox" not in other_where
