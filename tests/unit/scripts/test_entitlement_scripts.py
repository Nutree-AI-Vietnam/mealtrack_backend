"""Entitlement ledger scripts: worker ``--once`` and the backfill (NM-680)."""

import importlib.util
import json
import sys
from argparse import Namespace
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from tests.fixtures.fakes.fake_billing_provider import (
    NOW,
    FakeBillingProvider,
    FakeBudget,
    FakeClock,
    make_sub,
    make_user,
    snapshot,
)
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.services.entitlement_backfill_service import (
    BackfillCandidate,
    EntitlementBackfillService,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import (
    FLAG_ENT_RECONCILE,
    IdentityKind,
    SyncLane,
)
from src.domain.model.billing.ledger import BillingProviderEvent
from src.infra.services.rc_budget import RcBudget
from src.infra.workers.entitlement_worker import EntitlementWorker

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name):
    # Loaded by path: tests/unit/scripts shadows the project's ``scripts`` package.
    module_name = f"_nm680_{name}"
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


worker_script = _load("entitlement_worker")
backfill_script = _load("backfill_entitlement_ledger")

U1, U2, U3 = "user-1", "user-2", "user-3"
UID1, UID2, UID3 = "uid-1", "uid-2", "uid-3"
LEAD = "0b3c3a7e-5b1d-4d55-9a52-0d8c4f1b2a11"


class _Env:
    def __init__(self):
        self.uow = FakeUnitOfWork()
        for user_id, uid in ((U1, UID1), (U2, UID2), (U3, UID3)):
            self.uow.users.users[user_id] = make_user(user_id, uid)
        self.clock = FakeClock(NOW)
        self.provider = FakeBillingProvider()
        self.provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
        self.provider.responses[UID2] = snapshot(UID2)
        self.provider.responses[UID3] = ProviderUnavailable("down")
        self.budget = FakeBudget()
        self.sync = EntitlementSyncService(
            uow_factory=self.factory,
            provider=self.provider,
            budget=self.budget,
            clock=self.clock,
        )

    def factory(self):
        return self.uow

    def backfill_service(self):
        return EntitlementBackfillService(
            uow_factory=self.factory,
            sync_service=self.sync,
            provider=self.provider,
            budget=self.budget,
            clock=self.clock,
        )


@pytest.fixture
def env():
    return _Env()


CANDIDATES = [
    BackfillCandidate(user_id=U1, firebase_uid=UID1, legacy_active=True),
    BackfillCandidate(user_id=U2, firebase_uid=UID2, legacy_active=True),
    BackfillCandidate(user_id=U3, firebase_uid=UID3, legacy_active=False),
]


@pytest.fixture
def candidates(monkeypatch):
    async def fake_load(session, *, after_user_id, limit, now):
        remaining = [
            candidate
            for candidate in CANDIDATES
            if after_user_id is None or candidate.user_id > after_user_id
        ]
        return remaining[:limit]

    monkeypatch.setattr(backfill_script, "load_candidates", fake_load)


@asynccontextmanager
async def _no_session():
    yield None


def _args(tmp_path, **overrides):
    args = Namespace(
        dry_run=False,
        limit=None,
        page_size=2,
        after_user_id=None,
        resume=False,
        checkpoint=str(tmp_path / "checkpoint.json"),
        report=str(tmp_path / "report.jsonl"),
        rps=1.0,
        budget_timeout=1.0,
        provider_retries=0,
        provider_retry_wait=0.0,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _report(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "report.jsonl").read_text().splitlines()
    ]


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_the_diff_report_and_nothing_else(
    env, candidates, tmp_path
):
    summary = await backfill_script.run(
        _args(tmp_path, dry_run=True),
        service=env.backfill_service(),
        session_factory=_no_session,
    )

    rows = {row["user_id"]: row for row in _report(tmp_path)}
    assert summary["processed"] == 3
    assert summary["outcomes"] == {"match": 2, "provider_error": 1}
    assert rows[U1]["ledger_after"] is True and rows[U1]["rc_active"] is True
    assert rows[U1]["ledger_before"] is None
    assert rows[U2]["ledger_after"] is False
    assert "legacy_differs" in rows[U2]["notes"]
    assert rows[U3]["sync_error"] == "provider_unavailable"
    assert all(row["dry_run"] for row in rows.values())
    # No side effects: no ledger rows, identities or checkpoint.
    assert env.uow.store_subscriptions.rows == {}
    assert env.uow.user_entitlements.rows == {}
    assert env.uow.billing_identities.rows == {}
    assert not (tmp_path / "checkpoint.json").exists()
    assert set(env.budget.lanes) == {SyncLane.BACKFILL}


@pytest.mark.asyncio
async def test_backfill_writes_the_ledger_and_checkpoints(env, candidates, tmp_path):
    summary = await backfill_script.run(
        _args(tmp_path, limit=2),
        service=env.backfill_service(),
        session_factory=_no_session,
    )

    assert summary["processed"] == 2
    assert (await env.uow.user_entitlements.get(U1)).has_access
    assert not (await env.uow.user_entitlements.get(U2)).has_access
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["last_user_id"] == U2

    resumed = await backfill_script.run(
        _args(tmp_path, resume=True),
        service=env.backfill_service(),
        session_factory=_no_session,
    )

    assert resumed["processed"] == 1
    assert resumed["outcomes"] == {"provider_error": 1}
    assert resumed["stopped_at_user_id"] == U3
    assert [row["user_id"] for row in _report(tmp_path)] == [U1, U2, U3]
    assert set(env.budget.lanes) == {SyncLane.BACKFILL}
    # The failed user is not checkpointed: the next --resume retries it.
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["last_user_id"] == U2


class _Monotonic:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class _BlipProvider:
    """RevenueCat answers 503 for its first ``failures`` calls, then recovers."""

    provider = "revenuecat"

    def __init__(self, failures):
        self.failures = failures
        self.calls = []

    async def fetch_customer(self, customer_id):
        self.calls.append(customer_id)
        if len(self.calls) <= self.failures:
            raise ProviderUnavailable("503")
        return snapshot(customer_id, make_sub(customer_id))


@pytest.mark.asyncio
async def test_backfill_outage_never_checkpoints_unfetched_users(tmp_path, monkeypatch):
    """An outage that opens the RcBudget circuit (5 failures, 30 s) used to
    fail and checkpoint every following user in milliseconds."""
    uow = FakeUnitOfWork()
    candidates = []
    for index in range(20):
        user_id, uid = f"user-{index:03d}", f"uid-{index:03d}"
        uow.users.users[user_id] = make_user(user_id, uid)
        candidates.append(BackfillCandidate(user_id=user_id, firebase_uid=uid))

    async def fake_load(session, *, after_user_id, limit, now):
        rest = [
            c for c in candidates if after_user_id is None or c.user_id > after_user_id
        ]
        return rest[:limit]

    monkeypatch.setattr(backfill_script, "load_candidates", fake_load)
    mono = _Monotonic()
    budget = RcBudget(
        rate_per_second=1000.0, concurrency=1, clock=mono, sleep=mono.sleep
    )
    provider = _BlipProvider(failures=5)
    clock = FakeClock(NOW)
    sync = EntitlementSyncService(
        uow_factory=lambda: uow, provider=provider, budget=budget, clock=clock
    )
    service = EntitlementBackfillService(
        uow_factory=lambda: uow,
        sync_service=sync,
        provider=provider,
        budget=budget,
        clock=clock,
        budget_timeout=5.0,
    )
    args = _args(tmp_path, page_size=7, provider_retries=3, provider_retry_wait=30.0)

    first = await backfill_script.run(
        args, service=service, session_factory=_no_session, sleep=mono.sleep
    )

    # Four attempts for the first user, then the run stops without a checkpoint.
    assert first["outcomes"] == {"provider_error": 1}
    assert first["stopped_at_user_id"] == candidates[0].user_id
    assert len(provider.calls) == 4
    assert not (tmp_path / "checkpoint.json").exists()

    resumed = await backfill_script.run(
        _args(
            tmp_path,
            page_size=7,
            resume=True,
            provider_retries=3,
            provider_retry_wait=30.0,
        ),
        service=service,
        session_factory=_no_session,
        sleep=mono.sleep,
    )

    # The 5th failure opens the circuit; the retry waits it out and every
    # user is fetched once it closes.
    assert resumed["processed"] == 20
    assert resumed["outcomes"] == {"match": 20}
    assert "stopped_at_user_id" not in resumed
    for candidate in candidates:
        assert (await uow.user_entitlements.get(candidate.user_id)).has_access
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["last_user_id"] == candidates[-1].user_id


@pytest.mark.asyncio
async def test_backfill_seeds_and_fetches_legacy_lead_customers(env):
    env.provider.responses[UID2] = snapshot(UID2)
    env.provider.responses[LEAD] = snapshot(LEAD, make_sub(LEAD))
    service = env.backfill_service()

    row = await service.backfill(
        BackfillCandidate(user_id=U2, firebase_uid=UID2, legacy_customer_ids=(LEAD,)),
        dry_run=False,
    )

    identity = await env.uow.billing_identities.find_by_customer_id("revenuecat", LEAD)
    assert identity.kind == IdentityKind.LEGACY_LEAD and identity.user_id == U2
    assert env.provider.calls == [UID2, LEAD]
    assert row.ledger_after is True
    assert row.legacy_customer_ids == 1


def test_backfill_parser_defaults_are_safe():
    args = backfill_script.build_parser().parse_args([])

    assert args.dry_run is False
    assert args.rps == 0.5
    assert args.limit is None
    assert args.provider_retries == 3
    assert args.provider_retry_wait == 30.0


# ---- worker runner ----


def test_worker_once_prints_pass_counts(env, monkeypatch, capsys):
    env.uow.billing_events.rows["r1"] = BillingProviderEvent(
        id="r1",
        provider="revenuecat",
        provider_event_id="evt-1",
        event_type="RENEWAL",
        status="received",
        received_at=NOW,
        app_user_id=UID1,
        raw={"event": {"type": "RENEWAL", "app_user_id": UID1}},
    )
    captured = {}

    def build(**options):
        captured.update(options)
        return EntitlementWorker(
            uow_factory=env.factory,
            inbox=EntitlementInboxService(
                uow_factory=env.factory, sync_service=env.sync, clock=env.clock
            ),
            syncer=env.sync,
            flags=StaticFeatureFlagReader({FLAG_ENT_RECONCILE: True}),
            clock=env.clock,
        )

    monkeypatch.setattr(worker_script, "build_entitlement_worker", build)

    worker_script.main(["--once", "--batch-size", "5"])

    summary = json.loads(capsys.readouterr().out)
    assert summary["enabled"] is True
    assert summary["claimed"] == 1
    assert summary["outcomes"] == {"processed": 1}
    assert captured["batch_size"] == 5
    assert env.uow.billing_events.rows["r1"].status == "processed"
