"""Tests for scripts/reconcile_subscription_access.py.

The RevenueCat adapter, the session factory and the access sync are fakes;
the PostgreSQL integration test covers the real queries and sync.
"""

import importlib.util
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.domain.ports.subscription_service_port import (
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.infra.services.subscription_access_sync_service import (
    SubscriptionSyncOutcome,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def _load_script():
    path = Path(__file__).resolve().parents[3] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "reconcile_subscription_access", path / "reconcile_subscription_access.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_subscription_access"] = module
    spec.loader.exec_module(module)
    return module


rec = _load_script()


# --- Fakes ------------------------------------------------------------------


def _row(status="active", snapshot_at=None, sync_required_at=None, expires=None):
    return SimpleNamespace(
        status=status,
        expires_at=expires or NOW + timedelta(days=30),
        grace_period_expires_at=None,
        period_type="normal",
        provider_snapshot_at=snapshot_at,
        provider_sync_required_at=sync_required_at,
    )


class FakeDatabase:
    def __init__(self):
        self.users = {}
        self.rows = {}
        self.commits = 0
        self.rollbacks = 0

    def add_user(self, user_id, rows=(), *, firebase_uid=None, deleted=False):
        self.users[user_id] = SimpleNamespace(
            id=user_id,
            firebase_uid=firebase_uid if firebase_uid is not None else f"fb-{user_id}",
            email=f"{user_id}@example.com",
            is_active=not deleted,
            deleted_at=NOW if deleted else None,
        )
        self.rows[user_id] = list(rows)

    def session(self):
        return FakeSession(self)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeSession:
    def __init__(self, database):
        self.database = database
        self._user_id = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _model, user_id, **_options):
        self._user_id = user_id
        return self.database.users.get(user_id)

    async def scalars(self, _statement):
        if self._user_id is None:  # the population query
            return _Scalars(
                user.id
                for user in self.database.users.values()
                if user.deleted_at is None
            )
        return _Scalars(self.database.rows.get(self._user_id, []))

    async def commit(self):
        self.database.commits += 1

    async def rollback(self):
        self.database.rollbacks += 1


class FakeAdapter:
    """Answers per Firebase uid; a list answers successive calls in order."""

    def __init__(self, answers=None, default=None):
        self.answers = answers or {}
        self.default = default
        self.calls = []

    async def get_verified_snapshot(self, firebase_uid):
        self.calls.append(firebase_uid)
        answer = self.answers.get(firebase_uid, self.default)
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer or _snapshot(firebase_uid)


def _snapshot(firebase_uid="fb", absent=False):
    return RevenueCatSnapshot(
        None if absent else {"entitlements": {}, "subscriptions": {}},
        NOW,
        absent=absent,
        app_user_id=firebase_uid,
    )


class FakeSync:
    def __init__(self, outcomes=None, default=None):
        self.outcomes = outcomes or {}
        self.default = default or SubscriptionSyncOutcome(
            "applied", "unresolved", "granted"
        )
        self.calls = []

    async def __call__(self, session, user, *, snapshot, source):
        self.calls.append((user.id, source))
        outcome = self.outcomes.get(user.id, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _pacer(interval=0.0):
    clock = FakeClock()
    return rec.Pacer(interval, clock=clock, sleep=clock.sleep), clock


SUBSCRIBERS = rec.Population(rec.POPULATION_SUBSCRIBERS)


async def _run(database, user_ids, *, apply=False, adapter=None, sync=None, **kwargs):
    pacer = kwargs.pop("pacer", None) or _pacer()[0]
    stdout = kwargs.pop("stdout", None) or StringIO()
    return await rec.run_pass(
        user_ids,
        apply=apply,
        population=kwargs.pop("population", SUBSCRIBERS),
        session_factory=database.session,
        adapter=adapter or FakeAdapter(),
        pacer=pacer,
        sync=sync or FakeSync(),
        stdout=stdout,
        # The rows above are dated from NOW; the decisions must be too, or
        # the tests change verdict once the wall clock passes their expiry.
        clock=kwargs.pop("clock", lambda: NOW),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_stored_and_legacy_decisions_are_taken_at_the_runs_clock():
    """Both decisions use the injected clock, not the wall clock: at NOW + 31
    days the rows that expire at NOW + 30 days grant nothing."""
    database = FakeDatabase()
    database.add_user("verified", [_row(snapshot_at=NOW)])
    database.add_user("legacy", [_row()])
    path_records = StringIO()
    error = RevenueCatSnapshotUnavailable("bad key", reason="http_4xx")

    summary = await _run(
        database,
        ["verified", "legacy"],
        adapter=FakeAdapter(default=error),
        output=path_records,
        clock=lambda: NOW + timedelta(days=31),
    )

    records = {
        record["user_id"]: record
        for record in map(json.loads, path_records.getvalue().splitlines())
    }
    assert (records["verified"]["before"], records["verified"]["legacy_before"]) == (
        "denied",
        "denied",
    )
    assert (records["legacy"]["before"], records["legacy"]["legacy_before"]) == (
        "unresolved",
        "denied",
    )
    assert summary.counts["granted"] == 0


# --- Checkpoint (finding 37) ------------------------------------------------


def test_apply_refuses_to_resume_a_dry_run_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    dry_run = rec.Checkpoint.start(
        path, mode="dry-run", population=SUBSCRIBERS, now=NOW
    )
    dry_run.record("user-1")
    dry_run.close()

    with pytest.raises(rec.UsageError, match="dry-run pass"):
        rec.Checkpoint.resume(path, mode="apply", population=SUBSCRIBERS, now=NOW)


def test_dry_run_refuses_to_resume_an_apply_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    rec.Checkpoint.start(path, mode="apply", population=SUBSCRIBERS, now=NOW).close()

    with pytest.raises(rec.UsageError, match="apply pass"):
        rec.Checkpoint.resume(path, mode="dry-run", population=SUBSCRIBERS, now=NOW)


def test_resume_refuses_another_population_and_a_stale_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    rec.Checkpoint.start(path, mode="apply", population=SUBSCRIBERS, now=NOW).close()
    repair = rec.Population(rec.POPULATION_REPAIR, expiring_within_hours=24)

    with pytest.raises(rec.UsageError, match="population"):
        rec.Checkpoint.resume(path, mode="apply", population=repair, now=NOW)
    # A checkpoint from before a rollback would skip users whose rows the
    # old image rewrote.
    with pytest.raises(rec.UsageError, match="start a fresh pass"):
        rec.Checkpoint.resume(
            path,
            mode="apply",
            population=SUBSCRIBERS,
            now=NOW + rec.CHECKPOINT_MAX_AGE + timedelta(minutes=1),
        )


def test_resume_refuses_a_legacy_json_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"completed": ["user-1"]}))

    with pytest.raises(rec.UsageError, match="not a reconcile checkpoint"):
        rec.Checkpoint.resume(path, mode="apply", population=SUBSCRIBERS, now=NOW)


@pytest.mark.asyncio
async def test_resume_skips_completed_users_and_tolerates_a_torn_last_line(
    tmp_path,
):
    path = tmp_path / "checkpoint.jsonl"
    first = rec.Checkpoint.start(path, mode="apply", population=SUBSCRIBERS, now=NOW)
    first.record("user-1")
    first.close()
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"user_id": "user-')  # killed mid-write
    database = FakeDatabase()
    for user_id in ("user-1", "user-2"):
        database.add_user(user_id, [_row()])
    sync = FakeSync()

    resumed = rec.Checkpoint.resume(
        path, mode="apply", population=SUBSCRIBERS, now=NOW + timedelta(hours=1)
    )
    summary = await _run(
        database, ["user-1", "user-2"], apply=True, sync=sync, checkpoint=resumed
    )
    resumed.close()

    assert [user_id for user_id, _ in sync.calls] == ["user-2"]
    assert summary.counts["skipped"] == 1
    again = rec.Checkpoint.resume(
        path, mode="apply", population=SUBSCRIBERS, now=NOW + timedelta(hours=2)
    )
    assert again.completed == {"user-1", "user-2"}
    again.close()


@pytest.mark.asyncio
async def test_failed_users_are_not_checkpointed_so_resume_reads_them_again(
    tmp_path,
):
    database = FakeDatabase()
    database.add_user("ok", [_row()])
    database.add_user("down", [_row()])
    adapter = FakeAdapter(
        {"fb-down": RevenueCatSnapshotUnavailable("5xx", reason="http_5xx")}
    )
    checkpoint = rec.Checkpoint.start(
        tmp_path / "c.jsonl", mode="apply", population=SUBSCRIBERS, now=NOW
    )

    await _run(
        database,
        ["ok", "down"],
        apply=True,
        adapter=adapter,
        checkpoint=checkpoint,
        max_attempts=1,
    )
    checkpoint.close()

    assert checkpoint.completed == {"ok"}


@pytest.mark.asyncio
async def test_cli_apply_resume_on_a_dry_run_checkpoint_exits_2_without_reading(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "checkpoint.jsonl"
    rec.Checkpoint.start(
        path, mode="dry-run", population=SUBSCRIBERS, now=datetime.now(UTC)
    ).close()
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    adapter = FakeAdapter()
    adapter.api_key = "key"
    monkeypatch.setattr(
        "src.infra.database.config_async.AsyncSessionLocal", database.session
    )
    monkeypatch.setattr(
        "src.infra.adapters.revenuecat_adapter.RevenueCatAdapter", lambda: adapter
    )

    code = await rec.main(["--apply", "--resume", "--checkpoint", str(path)])

    assert code == rec.EXIT_USAGE
    assert "dry-run pass" in capsys.readouterr().err
    assert adapter.calls == []
    assert database.commits == 0


def _cli(monkeypatch, database, adapter, sync):
    adapter.api_key = "key"
    monkeypatch.setattr(
        "src.infra.database.config_async.AsyncSessionLocal", database.session
    )
    monkeypatch.setattr(
        "src.infra.adapters.revenuecat_adapter.RevenueCatAdapter", lambda: adapter
    )
    monkeypatch.setattr(rec, "sync_revenuecat_access", sync)


@pytest.mark.asyncio
async def test_cli_dry_run_writes_records_checkpoint_mode_and_a_no_go_line(
    tmp_path, monkeypatch, capsys
):
    database = FakeDatabase()
    database.add_user("paid", [_row()])
    database.add_user("legacy", [_row()])
    sync = FakeSync(
        {"legacy": RevenueCatSnapshotUnavailable("x", reason="conflicting_facts")}
    )
    _cli(monkeypatch, database, FakeAdapter(), sync)
    output, checkpoint = tmp_path / "dry.jsonl", tmp_path / "dry.checkpoint"

    code = await rec.main(
        [
            "--min-interval-seconds",
            "0",
            "--output",
            str(output),
            "--checkpoint",
            str(checkpoint),
        ]
    )

    assert code == rec.EXIT_NO_GO
    stdout = capsys.readouterr().out
    assert "candidates=2" in stdout
    assert (
        stdout.strip()
        .splitlines()[-1]
        .startswith(
            "RESULT: NO-GO mode=dry-run population=subscribers users=2 granted=1 "
            "denied=0 unresolved=1"
        )
    )
    assert len(output.read_text().splitlines()) == 2
    header = json.loads(checkpoint.read_text().splitlines()[0])
    assert header["mode"] == "dry-run"
    assert database.commits == 0


@pytest.mark.asyncio
async def test_cli_all_users_warns_about_creating_revenuecat_customers(
    monkeypatch, capsys
):
    database = FakeDatabase()
    database.add_user("never-bought")
    _cli(monkeypatch, database, FakeAdapter(), FakeSync())

    code = await rec.main(["--all-users", "--min-interval-seconds", "0"])

    assert code == rec.EXIT_GO
    assert "get-or-create" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_crash_is_incomplete_not_a_no_go_verdict(monkeypatch, capsys):
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    _cli(monkeypatch, database, FakeAdapter(), FakeSync({"user-1": KeyError("x")}))

    code = await rec.main(["--min-interval-seconds", "0"])

    assert code == rec.EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert "RESULT: INCOMPLETE stopped='crashed: KeyError'" in captured.out
    assert "Traceback" in captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["candidate_ids", "gate_counts", "checkpoint"])
async def test_cli_failure_before_the_pass_is_incomplete_not_no_go(
    tmp_path, monkeypatch, capsys, failing
):
    """Exit 1 means unresolved users exist; a database or file failure before
    the pass (population query, gate query, checkpoint) must not look like it."""
    database = FakeDatabase()
    _cli(monkeypatch, database, FakeAdapter(), FakeSync())

    async def broken(*_args, **_kwargs):
        raise ConnectionRefusedError("database is down")

    argv = ["--min-interval-seconds", "0"]
    if failing == "checkpoint":
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        argv += ["--checkpoint", str(blocker / "checkpoint.jsonl")]
    else:
        monkeypatch.setattr(rec, failing, broken)
        if failing == "gate_counts":
            argv = ["--gate"]

    code = await rec.main(argv)

    assert code == rec.EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert "RESULT: INCOMPLETE stopped='crashed: " in captured.out
    assert "Traceback" in captured.err


@pytest.mark.asyncio
async def test_cli_gate_reports_the_sql_counts(monkeypatch, capsys):
    async def counts(_factory):
        return {"unverified_grants": 0, "sync_required": 2}

    _cli(monkeypatch, FakeDatabase(), FakeAdapter(), FakeSync())
    monkeypatch.setattr(rec, "gate_counts", counts)

    code = await rec.main(["--gate"])

    assert code == rec.EXIT_NO_GO
    assert "GATE: NO-GO unverified_grants=0 sync_required=2" in capsys.readouterr().out


# --- Dry run vs apply, per-user output, gate (findings 36, 38) ---------------


@pytest.mark.asyncio
async def test_dry_run_rolls_back_every_snapshot_and_apply_commits():
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    dry_sync, apply_sync = FakeSync(), FakeSync()

    await _run(database, ["user-1"], apply=False, sync=dry_sync)
    assert (database.commits, database.rollbacks) == (0, 1)
    assert dry_sync.calls == [("user-1", "reconcile_dry_run")]

    await _run(database, ["user-1"], apply=True, sync=apply_sync)
    assert database.commits == 1
    assert apply_sync.calls == [("user-1", "reconcile")]


@pytest.mark.asyncio
async def test_each_user_gets_one_private_jsonl_record_without_pii(tmp_path):
    database = FakeDatabase()
    database.add_user("granted", [_row()])
    database.add_user("churned", [_row(status="expired", snapshot_at=NOW)])
    database.add_user("legacy", [_row()])
    sync = FakeSync(
        {
            "granted": SubscriptionSyncOutcome("applied", "unresolved", "granted"),
            "churned": SubscriptionSyncOutcome("absent", "denied", "denied"),
            "legacy": RevenueCatSnapshotUnavailable(
                "absence", reason="absent_with_history"
            ),
        }
    )
    path = tmp_path / "out" / "results.jsonl"
    output = rec._private_append(path, truncate=True)

    summary = await _run(
        database, ["granted", "churned", "legacy"], sync=sync, output=output
    )
    output.close()

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["user_id"], r["decision"], r["reason"]) for r in records] == [
        ("granted", "granted", "applied"),
        ("churned", "denied", "absent"),
        ("legacy", "unresolved", "absent_with_history"),
    ]
    assert records[2]["before"] == "unresolved"
    assert records[2]["snapshot"] == "ok"
    text = path.read_text()
    assert "@example.com" not in text and "fb-" not in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # The failing user is counted once, as unresolved (not also as granted).
    assert summary.counts == {
        "users": 3,
        "granted": 1,
        "denied": 1,
        "unresolved": 1,
        "downgraded": 0,
        "loses_current_access": 0,
        "failed": 1,
        "skipped": 0,
        "gone": 0,
    }


@pytest.mark.asyncio
async def test_gate_is_no_go_while_any_user_stays_unresolved():
    database = FakeDatabase()
    database.add_user("legacy", [_row()])
    database.add_user("ok", [_row()])
    stdout = StringIO()
    adapter = FakeAdapter(
        {"fb-legacy": RevenueCatSnapshotUnavailable("bad", reason="malformed")}
    )

    summary = await _run(database, ["legacy", "ok"], adapter=adapter, stdout=stdout)

    assert summary.exit_code == rec.EXIT_NO_GO
    assert summary.line().startswith("RESULT: NO-GO mode=dry-run")
    assert "unresolved=1" in summary.line()
    assert "unresolved user_id=legacy reason=malformed" in stdout.getvalue()


@pytest.mark.asyncio
async def test_gate_is_go_when_every_user_resolves_and_reports_downgrades():
    database = FakeDatabase()
    database.add_user("paid", [_row(snapshot_at=NOW)])
    database.add_user("lapsed", [_row(snapshot_at=NOW)])
    stdout = StringIO()
    sync = FakeSync(
        {
            "paid": SubscriptionSyncOutcome("unchanged", "granted", "granted"),
            "lapsed": SubscriptionSyncOutcome(
                "applied", "granted", "denied", rows_revoked=1
            ),
        }
    )

    summary = await _run(database, ["paid", "lapsed"], sync=sync, stdout=stdout)

    assert summary.exit_code == rec.EXIT_GO
    assert summary.line().startswith("RESULT: GO")
    assert summary.counts["downgraded"] == 1
    assert "downgrade user_id=lapsed" in stdout.getvalue()


@pytest.mark.asyncio
async def test_payer_denied_at_the_first_cutover_is_reported_as_losing_access(
    tmp_path,
):
    """Every legacy grant is unverified at the first cutover, so the policy's
    "before" is unresolved and a payer RevenueCat now denies is no policy
    downgrade. Today's (flag-off, legacy) check grants them: report the loss."""
    database = FakeDatabase()
    database.add_user("payer", [_row()])  # active, unexpired, never verified
    database.add_user("churned", [_row(status="expired")])
    stdout = StringIO()
    sync = FakeSync(
        {
            "payer": SubscriptionSyncOutcome("applied", "unresolved", "denied"),
            "churned": SubscriptionSyncOutcome("applied", "denied", "denied"),
        }
    )
    path = tmp_path / "results.jsonl"
    output = rec._private_append(path, truncate=True)

    summary = await _run(
        database, ["payer", "churned"], sync=sync, stdout=stdout, output=output
    )
    output.close()

    records = {
        record["user_id"]: record
        for record in map(json.loads, path.read_text().splitlines())
    }
    assert (
        records["payer"]["legacy_before"],
        records["payer"]["downgrade"],
        records["payer"]["loses_current_access"],
    ) == ("granted", False, True)
    assert records["churned"]["loses_current_access"] is False
    assert summary.counts["loses_current_access"] == 1
    assert summary.counts["downgraded"] == 0
    assert "loses_access user_id=payer" in stdout.getvalue()
    assert "user_id=churned" not in stdout.getvalue()
    assert "loses_current_access=1" in summary.line()


@pytest.mark.asyncio
async def test_unreadable_customer_keeps_the_stored_decision():
    database = FakeDatabase()
    database.add_user("verified", [_row(snapshot_at=NOW)])
    database.add_user("legacy", [_row()])
    error = RevenueCatSnapshotUnavailable("bad key", reason="http_4xx")

    summary = await _run(
        database,
        ["verified", "legacy"],
        apply=True,
        adapter=FakeAdapter(default=error),
    )

    assert summary.counts["granted"] == 1
    assert summary.counts["unresolved"] == 1
    assert summary.counts["failed"] == 2
    assert summary.exit_code == rec.EXIT_NO_GO
    assert database.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_error", "sync_error"),
    [
        # A revoked or rotated key: never retried.
        (RevenueCatSnapshotUnavailable("bad key", reason="http_4xx"), None),
        (RevenueCatSnapshotUnavailable("bad body", reason="malformed"), None),
        # Readable, but the snapshot cannot be applied.
        (None, RevenueCatSnapshotUnavailable("amazon", reason="unsupported_store")),
        # A live account whose identity changed under the read.
        (None, RevenueCatSnapshotUnavailable("moved", reason="owner_changed")),
        # Retryable, its retries used up (not enough in a row to stop the pass).
        (RevenueCatSnapshotUnavailable("down", reason="http_5xx"), None),
    ],
    ids=[
        "http_4xx",
        "malformed",
        "unsupported_store",
        "owner_changed",
        "http_5xx_exhausted",
    ],
)
async def test_a_failed_read_fails_the_verdict_even_when_the_stored_decision_resolves(
    adapter_error, sync_error
):
    """The stored decision of a verified user reads granted or denied, so the
    failure is not "unresolved"; the run still did not verify them."""
    database = FakeDatabase()
    database.add_user("verified", [_row(snapshot_at=NOW)])
    database.add_user("ok", [_row(snapshot_at=NOW)])
    stdout = StringIO()
    adapter = FakeAdapter({"fb-verified": adapter_error} if adapter_error else {})
    sync = FakeSync(
        {
            "verified": sync_error
            or SubscriptionSyncOutcome("applied", "granted", "granted"),
            "ok": SubscriptionSyncOutcome("unchanged", "granted", "granted"),
        }
    )

    summary = await _run(
        database,
        ["verified", "ok"],
        adapter=adapter,
        sync=sync,
        stdout=stdout,
        max_attempts=2,
    )

    reason = (adapter_error or sync_error).reason
    assert (summary.counts["granted"], summary.counts["unresolved"]) == (2, 0)
    assert summary.counts["failed"] == 1
    assert summary.exit_code == rec.EXIT_NO_GO
    assert summary.line().startswith("RESULT: NO-GO")
    assert "failed=1" in summary.line()
    assert f"failed user_id=verified reason={reason}" in stdout.getvalue()


@pytest.mark.asyncio
async def test_gone_accounts_stay_out_of_the_verdict(tmp_path):
    """A deleted or vanished account has no access left to verify: like the
    --gate SQL, the verdict ignores it, whatever its stored rows say."""
    database = FakeDatabase()
    database.add_user("deleted", [_row()], deleted=True)  # legacy: unresolved
    adapter = FakeAdapter()
    stdout = StringIO()
    checkpoint = rec.Checkpoint.start(
        tmp_path / "c.jsonl", mode="dry-run", population=SUBSCRIBERS, now=NOW
    )

    summary = await _run(
        database,
        ["deleted", "missing"],
        adapter=adapter,
        checkpoint=checkpoint,
        stdout=stdout,
    )
    checkpoint.close()

    assert adapter.calls == []
    assert summary.counts["users"] == 2
    assert summary.counts["gone"] == 2
    assert (
        summary.counts["granted"],
        summary.counts["denied"],
        summary.counts["unresolved"],
        summary.counts["failed"],
    ) == (0, 0, 0, 0)
    assert summary.exit_code == rec.EXIT_GO
    assert summary.line().startswith("RESULT: GO")
    assert "gone=2" in summary.line()
    assert stdout.getvalue() == ""
    # Nothing for --resume to read again.
    assert checkpoint.completed == {"deleted", "missing"}


@pytest.mark.asyncio
async def test_a_single_user_run_for_a_deleted_account_is_a_go():
    database = FakeDatabase()
    database.add_user("deleted", [_row()], deleted=True)

    summary = await _run(
        database,
        ["deleted"],
        population=rec.Population(rec.POPULATION_USER, user_id="deleted"),
    )

    assert summary.counts["gone"] == 1
    assert summary.exit_code == rec.EXIT_GO


class _DeletingAdapter(FakeAdapter):
    """Deletes (or removes) the account while its customer is being read."""

    def __init__(self, database, *, vanish=False):
        super().__init__()
        self.database = database
        self.vanish = vanish

    async def get_verified_snapshot(self, firebase_uid):
        user_id = firebase_uid.removeprefix("fb-")
        if self.vanish:
            del self.database.users[user_id]
        else:
            user = self.database.users[user_id]
            user.is_active, user.deleted_at = False, NOW
        return await super().get_verified_snapshot(firebase_uid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vanish", "reason"), [(False, "user_inactive"), (True, "user_missing")]
)
async def test_an_account_deleted_between_the_read_and_the_apply_is_gone(
    vanish, reason
):
    database = FakeDatabase()
    database.add_user("leaving", [_row()])
    sync = FakeSync()
    output = StringIO()

    summary = await _run(
        database,
        ["leaving"],
        apply=True,
        adapter=_DeletingAdapter(database, vanish=vanish),
        sync=sync,
        output=output,
    )

    assert sync.calls == []
    assert json.loads(output.getvalue())["reason"] == reason
    assert (summary.counts["gone"], summary.counts["failed"]) == (1, 0)
    assert summary.exit_code == rec.EXIT_GO
    assert database.commits == 0


@pytest.mark.asyncio
async def test_an_account_deleted_while_its_snapshot_is_applied_is_gone():
    """Deletion tombstones the Firebase uid, so a deletion that lands after
    the apply's liveness check fails the sync as an owner change."""
    database = FakeDatabase()
    database.add_user("leaving", [_row()])
    output = StringIO()

    class DeletedDuringSync(FakeSync):
        async def __call__(self, session, user, *, snapshot, source):
            database.users["leaving"].is_active = False
            database.users["leaving"].deleted_at = NOW
            raise RevenueCatSnapshotUnavailable("moved", reason="owner_changed")

    summary = await _run(
        database,
        ["leaving"],
        apply=True,
        sync=DeletedDuringSync(),
        output=output,
    )

    assert json.loads(output.getvalue())["reason"] == "user_inactive"
    assert (summary.counts["gone"], summary.counts["failed"]) == (1, 0)
    assert summary.exit_code == rec.EXIT_GO
    assert database.commits == 0


@pytest.mark.asyncio
async def test_a_live_user_without_a_firebase_uid_is_a_failure():
    database = FakeDatabase()
    database.add_user("no-uid", [_row(snapshot_at=NOW)], firebase_uid="")

    summary = await _run(database, ["no-uid"])

    assert summary.counts["failed"] == 1
    assert summary.exit_code == rec.EXIT_NO_GO


# --- RevenueCat pacing (Retry-After / 429) ----------------------------------


@pytest.mark.asyncio
async def test_retry_after_from_a_429_is_waited_before_the_next_request():
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    adapter = FakeAdapter(
        {
            "fb-user-1": [
                RevenueCatSnapshotUnavailable(
                    "429", retry_after_seconds=7, reason="http_429"
                ),
                _snapshot("fb-user-1"),
            ]
        }
    )
    pacer, clock = _pacer(interval=1.0)

    summary = await _run(database, ["user-1"], adapter=adapter, pacer=pacer)

    assert adapter.calls == ["fb-user-1", "fb-user-1"]
    assert clock.sleeps == [7]
    assert summary.counts["granted"] == 1


@pytest.mark.asyncio
async def test_exhausted_429_still_holds_the_next_user_for_retry_after():
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    database.add_user("user-2", [_row()])
    limited = RevenueCatSnapshotUnavailable(
        "429", retry_after_seconds=30, reason="http_429"
    )
    adapter = FakeAdapter({"fb-user-1": [limited, limited]})
    pacer, clock = _pacer(interval=1.0)

    summary = await _run(
        database, ["user-1", "user-2"], adapter=adapter, pacer=pacer, max_attempts=2
    )

    assert clock.sleeps == [30, 30]
    assert summary.counts["failed"] == 1


@pytest.mark.asyncio
async def test_retry_after_beyond_max_wait_stops_the_pass_incomplete():
    database = FakeDatabase()
    database.add_user("user-1", [_row()])
    database.add_user("user-2", [_row()])
    adapter = FakeAdapter(
        {
            "fb-user-1": RevenueCatSnapshotUnavailable(
                "429", retry_after_seconds=3600, reason="http_429"
            )
        }
    )

    summary = await _run(
        database, ["user-1", "user-2"], adapter=adapter, max_wait_seconds=600
    )

    assert adapter.calls == ["fb-user-1"]
    assert summary.exit_code == rec.EXIT_INCOMPLETE
    assert "RESULT: INCOMPLETE" in summary.line()


@pytest.mark.asyncio
async def test_a_provider_outage_stops_the_pass_instead_of_failing_everyone():
    database = FakeDatabase()
    user_ids = [f"user-{index}" for index in range(10)]
    for user_id in user_ids:
        database.add_user(user_id, [_row()])
    adapter = FakeAdapter(
        default=RevenueCatSnapshotUnavailable("down", reason="transport")
    )

    summary = await _run(database, user_ids, adapter=adapter, max_attempts=1)

    assert len(adapter.calls) == rec.MAX_CONSECUTIVE_PROVIDER_FAILURES
    assert summary.exit_code == rec.EXIT_INCOMPLETE


@pytest.mark.asyncio
async def test_pacer_spaces_requests_by_the_minimum_interval():
    pacer, clock = _pacer(interval=1.0)

    await pacer.wait()
    clock.now += 0.25
    await pacer.wait()

    assert clock.sleeps == [0.75]


# --- Populations -------------------------------------------------------------


def _sql(population):
    statement = rec.population_statement(population, NOW)
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_default_population_only_reads_users_with_revenuecat_rows():
    args = rec.parse_args([])
    population = rec.population_from_args(args)
    sql = _sql(population)

    assert population.kind == rec.POPULATION_SUBSCRIBERS
    assert "users.is_active IS true AND users.deleted_at IS NULL" in sql
    assert "EXISTS (SELECT subscriptions.id" in sql
    assert "subscriptions.provider = 'revenuecat'" in sql


def test_all_users_is_opt_in_and_unfiltered():
    population = rec.population_from_args(rec.parse_args(["--all-users"]))

    assert population.kind == rec.POPULATION_ALL_USERS
    assert "subscriptions" not in _sql(population)
    assert "get-or-create" in rec.ALL_USERS_WARNING


def test_repair_population_selects_rows_that_need_a_fresh_read():
    population = rec.population_from_args(
        rec.parse_args(
            [
                "--repair",
                "--expiring-within-hours",
                "6",
                "--modified-since",
                "2026-10-20T08:00:00Z",
            ]
        )
    )
    sql = _sql(population)

    assert "subscriptions.provider = 'revenuecat'" in sql
    assert "subscriptions.provider_sync_required_at IS NOT NULL" in sql
    assert (
        "subscriptions.status IN ('active', 'billing_issue', 'cancelled') "
        "AND subscriptions.provider_snapshot_at IS NULL"
    ) in sql
    assert (
        "subscriptions.expires_at BETWEEN '2026-10-26 06:00:00+00:00' "
        "AND '2026-10-26 18:00:00+00:00'"
    ) in sql
    assert "subscriptions.grace_period_expires_at BETWEEN" in sql
    assert "subscriptions.updated_at >= '2026-10-20 08:00:00+00:00'" in sql


def test_repair_defaults_to_a_24_hour_window_and_zero_turns_it_off():
    default = rec.population_from_args(rec.parse_args(["--repair"]))
    off = rec.population_from_args(
        rec.parse_args(["--repair", "--expiring-within-hours", "0"])
    )

    assert default.expiring_within_hours == 24
    assert "expires_at BETWEEN" not in _sql(off)
    assert "provider_sync_required_at IS NOT NULL" in _sql(off)


@pytest.mark.parametrize(
    "argv",
    [
        ["--expiring-within-hours", "6"],
        ["--modified-since", "2026-10-20T08:00:00Z"],
        ["--resume"],
        ["--repair", "--all-users"],
    ],
)
def test_invalid_argument_combinations_exit_2(argv):
    with pytest.raises(SystemExit) as raised:
        rec.parse_args(argv)
    assert raised.value.code == rec.EXIT_USAGE


# --- Runbook stays in sync with the script ----------------------------------

RUNBOOK = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "runbooks"
    / "subscription-access-reconciliation.md"
)


def test_runbook_quotes_the_go_no_go_sql_verbatim():
    assert rec.GO_NO_GO_SQL in RUNBOOK.read_text(encoding="utf-8")


def test_every_runbook_command_parses():
    import re
    import shlex

    text = RUNBOOK.read_text(encoding="utf-8").replace("\\\n", " ")
    commands = [
        line.split("python -m scripts.reconcile_subscription_access", 1)[1]
        for line in text.splitlines()
        if "python -m scripts.reconcile_subscription_access" in line
        and not line.strip().startswith("`")
    ]

    assert len(commands) >= 5
    for command in commands:
        argv = shlex.split(re.sub(r"<[^>]+>", "2026-10-26T00:00:00Z", command))
        argv = [arg.rstrip("`") for arg in argv if arg != "`"]
        rec.parse_args(argv)
