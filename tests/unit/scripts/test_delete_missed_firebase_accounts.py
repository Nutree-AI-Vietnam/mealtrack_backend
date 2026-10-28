"""Tests for the missed Firebase account deletion script."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql


def _import_script():
    """Import by path: tests/unit/scripts shadows the scripts package."""
    path = Path(__file__).parents[3] / "scripts" / "delete_missed_firebase_accounts.py"
    spec = importlib.util.spec_from_file_location(
        "delete_missed_firebase_accounts", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _import_script()
Candidate = script.Candidate
FirebaseAccount = script.FirebaseAccount
Outcome = script.Outcome

REQUESTED = datetime(2026, 10, 12, 15, 38, 56, tzinfo=UTC)


def _sql(query) -> str:
    return str(
        query.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_parse_uids_file_skips_comments_and_reads_utc_times():
    candidates = script.parse_uids_file(
        ["# header", "", "uid-a,2026-10-12T15:38:56Z", " uid-b , 2026-10-13 "]
    )

    assert candidates == [
        Candidate("uid-a", REQUESTED, "logs"),
        Candidate("uid-b", datetime(2026, 10, 13, tzinfo=UTC), "logs"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "uid-a",
        '"uid-a",2026-10-12',
        "deleted:3f2c,2026-10-12",
        "uid a,2026-10-12",
        f"{'u' * 129},2026-10-12",
    ],
)
def test_parse_uids_file_rejects_lines_that_are_not_a_plain_uid(line):
    with pytest.raises(ValueError):
        script.parse_uids_file([line])


def test_merge_keeps_one_candidate_per_uid_with_the_latest_request():
    day = timedelta(days=1)
    merged = script.merge_candidates(
        [
            Candidate("uid-a", REQUESTED, "logs"),
            Candidate("uid-b", REQUESTED - day, "logs"),
        ],
        [Candidate("uid-a", REQUESTED - 2 * day, "database")],
    )

    assert merged == [
        Candidate("uid-b", REQUESTED - day, "logs"),
        Candidate("uid-a", REQUESTED, "logs"),
    ]


@pytest.mark.parametrize(
    ("recorded", "active", "seen_after", "allow_seen", "expected"),
    [
        (False, False, None, False, Outcome.UNCONFIRMED),
        (True, True, timedelta(0), False, Outcome.ACTIVE_ACCOUNT),
        (True, False, "gone", False, Outcome.ALREADY_DELETED),
        (True, False, timedelta(hours=2), False, Outcome.SEEN_AFTER_REQUEST),
        (True, False, timedelta(hours=2), True, Outcome.PENDING),
        (True, False, timedelta(minutes=5), False, Outcome.PENDING),
    ],
)
def test_classify(recorded, active, seen_after, allow_seen, expected):
    candidate = Candidate("uid-a", REQUESTED, "logs")
    account = (
        None
        if seen_after == "gone"
        else FirebaseAccount(
            "uid-a", REQUESTED + seen_after if seen_after else None, ("apple.com",)
        )
    )

    outcome = script.classify(
        candidate,
        deletion_recorded=recorded,
        holds_active_account=active,
        account=account,
        delete_seen_after_request=allow_seen,
    )

    assert outcome is expected


def test_queries_filter_on_activity_tombstones_and_time():
    candidate = Candidate("uid-a", REQUESTED, "logs")

    inactive = _sql(script.inactive_candidates_query(REQUESTED))
    record = _sql(script.deletion_record_query(candidate))
    active = _sql(script.active_account_query("uid-a"))

    assert "users.is_active IS false" in inactive
    assert "users.firebase_uid NOT LIKE 'deleted:%%'" in inactive
    assert "users.deleted_at >= '2026-10-12 15:38:56+00:00'" in inactive
    assert "users.is_active IS false" in record
    assert "users.firebase_uid = 'uid-a'" in record
    assert "users.firebase_uid LIKE 'deleted:%%'" in record
    assert (
        "users.deleted_at BETWEEN '2026-10-12 15:38:46+00:00' "
        "AND '2026-10-12 15:39:06+00:00'"
    ) in record
    assert "users.is_active IS true" in active
    assert "users.firebase_uid = 'uid-a'" in active


def _fakes(*, recorded=(), active=(), accounts=None, failing=()):
    accounts = accounts or {}
    deleted: list[str] = []

    async def is_recorded(candidate):
        return candidate.firebase_uid in recorded

    async def is_active(uid):
        return uid in active

    def lookup(uid):
        if uid in failing:
            raise RuntimeError("quota exceeded")
        return accounts.get(uid)

    return deleted, {
        "recorded": is_recorded,
        "active": is_active,
        "lookup": lookup,
        "delete": deleted.append,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_run_deletes_only_confirmed_unused_accounts_and_only_with_apply(apply):
    live = FirebaseAccount("live", REQUESTED, ("apple.com",))
    candidates = [
        Candidate(uid, REQUESTED, "logs")
        for uid in ("live", "gone", "reused", "unrecorded", "returned")
    ]
    deleted, fakes = _fakes(
        recorded={"live", "gone", "reused", "returned"},
        active={"reused"},
        accounts={
            "live": live,
            "reused": live,
            "unrecorded": live,
            "returned": FirebaseAccount("returned", REQUESTED + timedelta(days=1), ()),
        },
    )
    rows: list[dict] = []

    counts = await script.run(candidates, apply=apply, emit=rows.append, **fakes)

    assert {row["firebase_uid"]: row["outcome"] for row in rows} == {
        "live": "deleted" if apply else "pending_deletion",
        "gone": "already_deleted",
        "reused": "skipped_active_account",
        "unrecorded": "skipped_no_deletion_record",
        "returned": "review_seen_after_request",
    }
    assert deleted == (["live"] if apply else [])
    assert sum(counts.values()) == 5


@pytest.mark.asyncio
async def test_run_records_each_error_and_stops_after_consecutive_failures():
    candidates = [
        Candidate(uid, REQUESTED, "logs") for uid in ("ok", "e1", "e2", "e3", "never")
    ]
    deleted, fakes = _fakes(
        recorded={"ok", "e1", "e2", "e3", "never"},
        accounts={"ok": FirebaseAccount("ok", REQUESTED, ())},
        failing={"e1", "e2", "e3"},
    )
    rows: list[dict] = []

    await script.run(candidates, apply=True, emit=rows.append, **fakes)

    assert [row.get("outcome") for row in rows[:4]] == [
        "deleted",
        "error",
        "error",
        "error",
    ]
    assert rows[1]["error"] == "RuntimeError: quota exceeded"
    assert rows[4] == {"stopped": "3 consecutive errors"}
    assert deleted == ["ok"]
