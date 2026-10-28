"""scripts/reconcile_subscription_access.py on real PostgreSQL.

Runs the script's population queries, the real access sync and the runbook's
go/no-go SQL. Only the RevenueCat adapter's HTTP transport is stubbed, with
the documented v1 response shape.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    standard_annual_response,
)

from src.domain.model.user.firebase_identity import tombstone_firebase_uid
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    sync_revenuecat_access,
)

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "reconcile_subscription_access_pg",
        _SCRIPT / "reconcile_subscription_access.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_subscription_access_pg"] = module
    spec.loader.exec_module(module)
    return module


rec = _load_script()


@pytest_asyncio.fixture
async def sessions(
    pg_session, async_session_factory
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions configured like the app's: no autoflush, no expiry on commit."""
    yield async_sessionmaker(
        bind=async_session_factory.kw["bind"],
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@contextmanager
def _revenuecat(bodies: dict[str, Any]):
    """Answer GET /subscribers/{uid} from ``bodies``; anyone else is new."""

    async def get(url, **_kwargs):
        uid = url.rsplit("/", 1)[-1]
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = bodies.get(uid) or empty_customer_response(
            app_user_id=uid
        )
        return response

    with patch("httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get)
        yield client.return_value.__aenter__.return_value.get


async def _user(factory, *, active=True) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"buyer-{uuid4()}@example.com",
        username=f"buyer-{uuid4()}",
        password_hash="unused",
        is_active=active,
        deleted_at=None if active else datetime.now(UTC),
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
    return user


async def _row(factory, user: User, *, provider="revenuecat", **values) -> None:
    now = datetime.now(UTC)
    defaults = {
        "id": str(uuid4()),
        "user_id": user.id,
        "provider": provider,
        "revenuecat_subscriber_id": (
            user.firebase_uid if provider == "revenuecat" else None
        ),
        "product_id": "annual",
        "platform": "ios",
        "status": "active",
        "purchased_at": now - timedelta(days=300),
        "expires_at": now + timedelta(days=60),
        "is_sandbox": False,
        "updated_at": now - timedelta(days=10),
    }
    async with factory() as session:
        session.add(Subscription(**(defaults | values)))
        await session.commit()


async def _rows(factory, user: User) -> list[Subscription]:
    async with factory() as session:
        return list(
            (
                await session.scalars(
                    select(Subscription).where(Subscription.user_id == user.id)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_populations_select_the_intended_users(sessions):
    now = datetime.now(UTC)
    verified = now - timedelta(days=1)
    legacy = await _user(sessions)
    await _row(sessions, legacy)  # never verified
    settled = await _user(sessions)
    await _row(sessions, settled, provider_snapshot_at=verified)
    expiring = await _user(sessions)
    await _row(
        sessions,
        expiring,
        provider_snapshot_at=verified,
        expires_at=now + timedelta(hours=3),
    )
    marked = await _user(sessions)
    await _row(
        sessions,
        marked,
        status="expired",
        provider_snapshot_at=verified,
        provider_sync_required_at=now - timedelta(minutes=5),
    )
    touched = await _user(sessions)
    await _row(
        sessions,
        touched,
        provider_snapshot_at=verified,
        updated_at=now - timedelta(hours=1),
    )
    momo_only = await _user(sessions)
    await _row(sessions, momo_only, provider="momo", provider_snapshot_at=verified)
    never_bought = await _user(sessions)
    deleted = await _user(sessions, active=False)
    await _row(sessions, deleted)

    async def ids(population):
        return set(await rec.candidate_ids(sessions, population, now))

    assert await ids(rec.Population(rec.POPULATION_SUBSCRIBERS)) == {
        legacy.id,
        settled.id,
        expiring.id,
        marked.id,
        touched.id,
    }
    assert await ids(
        rec.Population(rec.POPULATION_REPAIR, expiring_within_hours=24)
    ) == {legacy.id, expiring.id, marked.id}
    assert await ids(
        rec.Population(
            rec.POPULATION_REPAIR,
            expiring_within_hours=0,
            modified_since=now - timedelta(hours=2),
        )
    ) == {legacy.id, marked.id, touched.id}
    all_users = await ids(rec.Population(rec.POPULATION_ALL_USERS))
    assert {momo_only.id, never_bought.id} <= all_users
    assert deleted.id not in all_users


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_apply_stamps_rows_and_the_gate_opens(
    sessions,
):
    now = datetime.now(UTC)
    paid = await _user(sessions)
    await _row(sessions, paid)
    churned = await _user(sessions)
    await _row(sessions, churned, status="expired", expires_at=now - timedelta(days=9))
    lost = await _user(sessions)  # RevenueCat holds nothing under this uid
    await _row(sessions, lost)
    momo = await _user(sessions)
    await _row(
        sessions,
        momo,
        provider="momo",
        product_id="momo_30d",
        platform="web",
        provider_snapshot_at=now,
    )
    bodies = {
        paid.firebase_uid: standard_annual_response(
            request_date=now, expires_date=now + timedelta(days=200)
        )
    }
    users = await rec.candidate_ids(
        sessions, rec.Population(rec.POPULATION_SUBSCRIBERS), now
    )
    assert momo.id not in users

    async def run(apply: bool):
        with _revenuecat(bodies) as get:
            summary = await rec.run_pass(
                users,
                apply=apply,
                population=rec.Population(rec.POPULATION_SUBSCRIBERS),
                session_factory=sessions,
                adapter=RevenueCatAdapter(api_key="test-key"),
                pacer=rec.Pacer(0),
                stdout=StringIO(),
            )
        return summary, get

    assert await rec.gate_counts(sessions) == {
        "unverified_grants": 2,
        "sync_required": 0,
    }

    dry, get = await run(apply=False)

    assert get.await_count == 3  # never the MoMo-only user
    assert dry.counts["granted"] == 1
    assert dry.counts["denied"] == 1
    assert dry.counts["unresolved"] == 1  # 'lost': absence over a possible grant
    assert dry.exit_code == rec.EXIT_NO_GO
    assert all(row.provider_snapshot_at is None for row in await _rows(sessions, paid))
    assert (await rec.gate_counts(sessions))["unverified_grants"] == 2

    applied, _ = await run(apply=True)

    assert applied.counts == dry.counts
    (paid_row,) = await _rows(sessions, paid)
    assert paid_row.provider_snapshot_at is not None
    assert paid_row.status == "active"
    (momo_row,) = await _rows(sessions, momo)
    assert momo_row.provider_snapshot_at == now  # never touched
    line, code = rec.gate_line(await rec.gate_counts(sessions))
    assert (line, code) == (
        "GATE: NO-GO unverified_grants=1 sync_required=0",
        rec.EXIT_NO_GO,
    )

    # The operator resolves the identity (here: RevenueCat now holds the
    # purchase under this uid); the repair pass clears the last row.
    bodies[lost.firebase_uid] = standard_annual_response(
        request_date=now + timedelta(seconds=5),
        expires_date=now + timedelta(days=30),
    )
    repair = rec.Population(rec.POPULATION_REPAIR, expiring_within_hours=24)
    assert await rec.candidate_ids(sessions, repair, now) == [lost.id]
    with _revenuecat(bodies):
        final = await rec.run_pass(
            [lost.id],
            apply=True,
            population=repair,
            session_factory=sessions,
            adapter=RevenueCatAdapter(api_key="test-key"),
            pacer=rec.Pacer(0),
            stdout=StringIO(),
        )

    assert final.exit_code == rec.EXIT_GO
    assert rec.gate_line(await rec.gate_counts(sessions)) == (
        "GATE: GO unverified_grants=0 sync_required=0",
        rec.EXIT_GO,
    )


@pytest.mark.asyncio
async def test_dry_run_reports_a_payer_revenuecat_now_denies(sessions, tmp_path):
    """First cutover: the legacy row grants today (flag off) but was never
    verified, so the policy calls it unresolved and the loss is no policy
    downgrade. The dry run must still name the user before --apply."""
    now = datetime.now(UTC)
    payer = await _user(sessions)
    await _row(sessions, payer)  # active, 60 days left, never verified
    bodies = {
        payer.firebase_uid: standard_annual_response(
            request_date=now, expires_date=now - timedelta(days=2)
        )
    }
    output_path = tmp_path / "dry.jsonl"
    output = rec._private_append(output_path, truncate=True)
    stdout = StringIO()

    with _revenuecat(bodies):
        summary = await rec.run_pass(
            [payer.id],
            apply=False,
            population=rec.Population(rec.POPULATION_SUBSCRIBERS),
            session_factory=sessions,
            adapter=RevenueCatAdapter(api_key="test-key"),
            pacer=rec.Pacer(0),
            output=output,
            stdout=stdout,
        )
    output.close()

    (record,) = [json.loads(line) for line in output_path.open()]
    assert (record["legacy_before"], record["before"], record["decision"]) == (
        "granted",
        "unresolved",
        "denied",
    )
    assert (record["downgrade"], record["loses_current_access"]) == (False, True)
    assert summary.counts["loses_current_access"] == 1
    assert f"loses_access user_id={payer.id}" in stdout.getvalue()
    (row,) = await _rows(sessions, payer)
    assert (row.status, row.provider_snapshot_at) == ("active", None)  # dry run


@pytest.mark.asyncio
async def test_an_account_deleted_while_its_snapshot_is_applied_is_gone(sessions):
    """Deletion tombstones the Firebase uid. One that commits after the
    apply's liveness check makes the locked sync see an owner change; the
    account is gone, not a failed read, and nothing is applied."""
    now = datetime.now(UTC)
    user = await _user(sessions)
    await _row(sessions, user)
    bodies = {
        user.firebase_uid: standard_annual_response(
            request_date=now, expires_date=now + timedelta(days=200)
        )
    }

    async def delete_then_sync(session, locked_user, **kwargs):
        async with sessions() as other:
            await other.execute(
                update(User)
                .where(User.id == user.id)
                .values(
                    firebase_uid=tombstone_firebase_uid(user.id),
                    is_active=False,
                    deleted_at=now,
                )
            )
            await other.commit()
        return await sync_revenuecat_access(session, locked_user, **kwargs)

    output = StringIO()
    with _revenuecat(bodies):
        summary = await rec.run_pass(
            [user.id],
            apply=True,
            population=rec.Population(rec.POPULATION_USER, user_id=user.id),
            session_factory=sessions,
            adapter=RevenueCatAdapter(api_key="test-key"),
            pacer=rec.Pacer(0),
            sync=delete_then_sync,
            output=output,
            stdout=StringIO(),
        )

    assert json.loads(output.getvalue())["reason"] == "user_inactive"
    assert (summary.counts["gone"], summary.counts["failed"]) == (1, 0)
    assert summary.exit_code == rec.EXIT_GO
    (row,) = await _rows(sessions, user)
    assert row.provider_snapshot_at is None


@pytest.mark.asyncio
async def test_repair_applies_the_emptied_customer_of_an_unresolved_transfer_source(
    sessions,
):
    """A TRANSFER moved the user's purchase away while its own RevenueCat read
    failed: the verified row is still 'active', marked, and remembered as a
    transfer source. --repair reads the now-empty customer and, because of
    the transfer, applies it instead of reporting absent_with_history."""
    now = datetime.now(UTC)
    moved_at = now - timedelta(minutes=10)
    source = await _user(sessions)
    await _row(
        sessions,
        source,
        provider_snapshot_at=now - timedelta(days=1),
        provider_sync_required_at=moved_at,
        provider_transfer_source_at=moved_at,
    )
    repair = rec.Population(rec.POPULATION_REPAIR, expiring_within_hours=24)
    assert await rec.candidate_ids(sessions, repair, now) == [source.id]
    output = StringIO()

    with _revenuecat({}):  # every customer is empty
        summary = await rec.run_pass(
            [source.id],
            apply=True,
            population=repair,
            session_factory=sessions,
            adapter=RevenueCatAdapter(api_key="test-key"),
            pacer=rec.Pacer(0),
            output=output,
            stdout=StringIO(),
        )

    record = json.loads(output.getvalue())
    assert (record["reason"], record["snapshot"], record["decision"]) == (
        "applied",
        "absent",
        "denied",
    )
    assert summary.exit_code == rec.EXIT_GO
    (row,) = await _rows(sessions, source)
    assert row.status == "expired"
    assert (row.provider_sync_required_at, row.provider_transfer_source_at) == (
        None,
        None,
    )
    assert rec.gate_line(await rec.gate_counts(sessions)) == (
        "GATE: GO unverified_grants=0 sync_required=0",
        rec.EXIT_GO,
    )
