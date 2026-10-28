from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.infra.repositories.subscription_repository_async import (
    AsyncSubscriptionRepository,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _AsyncSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.rows)


def test_async_subscription_repository_satisfies_port_contract():
    assert AsyncSubscriptionRepository.__abstractmethods__ == frozenset()


@pytest.mark.asyncio
async def test_find_expiring_in_window_uses_active_expiry_bounds():
    rows = [MagicMock(id="sub-1")]
    session = _AsyncSession(rows)
    repo = AsyncSubscriptionRepository(session)
    now = datetime(2026, 6, 9, tzinfo=UTC)

    result = await repo.find_expiring_in_window(from_days=1, to_days=2, now=now)

    assert result == rows
    compiled = str(session.statement)
    assert "subscriptions.status" in compiled
    assert "subscriptions.expires_at >= " in compiled
    assert "subscriptions.expires_at < " in compiled


@pytest.mark.asyncio
async def test_find_expiring_soon_returns_active_future_rows():
    rows = [MagicMock(id="sub-2")]
    session = _AsyncSession(rows)
    repo = AsyncSubscriptionRepository(session)

    result = await repo.find_expiring_soon(days_until_expiry=7)

    assert result == rows
    compiled = str(session.statement)
    assert "subscriptions.status" in compiled
    assert "subscriptions.expires_at <= " in compiled
    assert "subscriptions.expires_at > " in compiled


class _FirstResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _FirstSession:
    def __init__(self, row):
        self.row = row
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _FirstResult(self.row)


def _literal_sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.asyncio
async def test_find_by_revenuecat_id_never_selects_another_providers_row():
    session = _FirstSession(MagicMock(id="sub-3"))
    repo = AsyncSubscriptionRepository(session)

    await repo.find_by_revenuecat_id("firebase-uid", user_id="user-1")

    sql = _literal_sql(session.statement)
    assert "subscriptions.revenuecat_subscriber_id = 'firebase-uid'" in sql
    assert "subscriptions.provider = 'revenuecat'" in sql
    assert "subscriptions.user_id = 'user-1'" in sql
    assert "ORDER BY subscriptions.purchased_at DESC" in sql


class _SqliteSession:
    """Runs the repository's statements on an in-memory SQLite database."""

    def __init__(self, session):
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)


@pytest.fixture
def sqlite_rows():
    """Adds subscriptions rows to a real SQL engine; yields (add, repo)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.infra.database.models.subscription import Subscription

    engine = create_engine("sqlite://")
    Subscription.__table__.create(engine)
    session = Session(engine)
    purchased = datetime(2026, 1, 1, tzinfo=UTC)

    def add(row_id, product_id, *, days_newer=0, provider="revenuecat"):
        session.add(
            Subscription(
                id=row_id,
                user_id="user-1",
                revenuecat_subscriber_id="firebase-uid",
                provider=provider,
                product_id=product_id,
                platform="android",
                status="active",
                purchased_at=purchased + timedelta(days=days_newer),
                is_sandbox=False,
            )
        )
        session.flush()

    yield add, AsyncSubscriptionRepository(_SqliteSession(session))
    session.close()
    engine.dispose()


async def _found(repo, product_id):
    row = await repo.find_by_revenuecat_id(
        "firebase-uid", user_id="user-1", product_id=product_id
    )
    return row.id if row else None


@pytest.mark.asyncio
async def test_find_by_revenuecat_id_prefers_the_events_product_over_a_newer_row(
    sqlite_rows,
):
    """An event for the old product after an upgrade selects the old row; the
    newest row is only the fallback."""
    add, repo = sqlite_rows
    add("monthly", "onemonth")
    add("annual", "annual", days_newer=300)

    assert await _found(repo, "onemonth") == "monthly"
    assert await _found(repo, "annual") == "annual"
    assert await _found(repo, "lifetime") == "annual"  # newest
    assert await _found(repo, None) == "annual"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "event"),
    [
        # Snapshot rows hold v1 /subscribers' bare id; Play webhooks add the
        # base plan.
        ("premium_monthly", "premium_monthly:p1m"),
        # Rows the legacy handlers wrote from a webhook carry the base plan;
        # the bare id comes from a snapshot or a pre-2023 Play product.
        ("premium_monthly:p1m", "premium_monthly"),
        ("premium_monthly:p1m", "premium_monthly:p1m-promo"),
    ],
)
async def test_find_by_revenuecat_id_matches_play_products_with_or_without_base_plan(
    sqlite_rows, stored, event
):
    add, repo = sqlite_rows
    add("monthly", stored)
    add("annual", "premium_annual:p1y", days_newer=300)

    assert await _found(repo, event) == "monthly"


@pytest.mark.asyncio
async def test_the_exact_product_id_wins_over_the_same_play_product(sqlite_rows):
    add, repo = sqlite_rows
    add("bare", "premium_monthly", days_newer=10)
    add("with-plan", "premium_monthly:p1m")

    assert await _found(repo, "premium_monthly:p1m") == "with-plan"
    assert await _found(repo, "premium_monthly") == "bare"


@pytest.mark.asyncio
async def test_a_product_prefix_is_not_the_same_play_product(sqlite_rows):
    """'premium' is not 'premium_monthly', and LIKE wildcards in an id
    ('_') never match other characters."""
    add, repo = sqlite_rows
    add("monthly", "premium_monthly")
    add("other", "premiumXmonthly:p1m")
    add("annual", "premium_annual", days_newer=300)

    assert await _found(repo, "premium:p1m") == "annual"  # newest fallback
    assert await _found(repo, "premium_monthly:p1m") == "monthly"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, ""])
async def test_find_by_revenuecat_id_without_an_id_matches_nothing(missing):
    """`column == None` compiles to IS NULL, which would select rows another
    provider wrote with no RevenueCat id."""
    session = _AsyncSession([MagicMock(id="momo-row")])
    repo = AsyncSubscriptionRepository(session)

    assert await repo.find_by_revenuecat_id(missing, user_id="user-1") is None
    assert session.statement is None


def test_subscription_model_allows_rows_without_a_revenuecat_id():
    from src.infra.database.models.subscription import Subscription

    column = Subscription.__table__.c.revenuecat_subscriber_id
    assert column.nullable is True
    provider = Subscription.__table__.c.provider
    # No ORM-side default: every writer names its provider.
    assert provider.default is None
    assert provider.server_default is not None


@pytest.mark.parametrize("provider", [None, ""])
def test_insert_without_a_provider_is_refused(provider):
    from src.infra.database.models.subscription import (
        Subscription,
        _require_explicit_provider,
    )

    row = Subscription(user_id="user-1", product_id="momo_pass_30d", provider=provider)
    with pytest.raises(ValueError, match="provider must be set explicitly"):
        _require_explicit_provider(None, None, row)


def test_insert_with_a_provider_is_allowed():
    from src.infra.database.models.subscription import (
        Subscription,
        _require_explicit_provider,
    )

    row = Subscription(user_id="user-1", product_id="momo_pass_30d", provider="momo")
    _require_explicit_provider(None, None, row)
