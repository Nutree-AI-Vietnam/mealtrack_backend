"""Tests for the Play Store subscription backfill script."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql


def _import_script():
    """Import by path: tests/unit/scripts shadows the scripts package."""
    path = (
        Path(__file__).parents[3] / "scripts" / "backfill_play_store_subscriptions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_play_store_subscriptions", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _import_script()
Outcome = script.Outcome

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _entry(**overrides) -> dict:
    entry = {
        "store": "play_store",
        "is_sandbox": False,
        "original_purchase_date": _iso(NOW - 30 * DAY),
        "purchase_date": _iso(NOW - 2 * DAY),
        "expires_date": _iso(NOW + 300 * DAY),
        "grace_period_expires_date": None,
        "billing_issues_detected_at": None,
        "unsubscribe_detected_at": None,
        "refunded_at": None,
        "product_plan_identifier": "yearly",
        "store_transaction_id": "GPA.3374-2569-0717-66645",
    }
    entry.update(overrides)
    return entry


def _subscriber(**subscriptions) -> dict:
    return {"subscriber": {"subscriptions": subscriptions}}


def _sql(query) -> str:
    return str(
        query.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_parse_uids_file_skips_comments_and_keeps_the_first_of_each_uid():
    assert script.parse_uids_file(
        ["# android payers", "", " uid_a ", "uid-b", "uid_a"]
    ) == ["uid_a", "uid-b"]


@pytest.mark.parametrize(
    "line",
    ["$RCAnonymousID:09754064", "uid a", "uid/a", "uid,2026-10-29", "u" * 129],
)
def test_parse_uids_file_rejects_anything_but_a_plain_uid(line):
    with pytest.raises(ValueError):
        script.parse_uids_file([line])


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "active"),
        ({"unsubscribe_detected_at": _iso(NOW - DAY)}, "cancelled"),
        ({"expires_date": _iso(NOW - DAY)}, "expired"),
        (
            {
                "expires_date": _iso(NOW - DAY),
                "unsubscribe_detected_at": _iso(NOW - 5 * DAY),
            },
            "expired",
        ),
        ({"billing_issues_detected_at": _iso(NOW - DAY)}, "billing_issue"),
        (
            {
                "expires_date": _iso(NOW - DAY),
                "grace_period_expires_date": _iso(NOW + 2 * DAY),
                "billing_issues_detected_at": _iso(NOW - DAY),
            },
            "billing_issue",
        ),
        (
            {
                "expires_date": _iso(NOW - 10 * DAY),
                "grace_period_expires_date": _iso(NOW - 3 * DAY),
                "billing_issues_detected_at": _iso(NOW - 10 * DAY),
            },
            "expired",
        ),
        ({"refunded_at": _iso(NOW - DAY)}, "refunded"),
    ],
)
def test_subscription_status_matches_what_the_webhooks_would_leave(overrides, expected):
    assert script.subscription_status(_entry(**overrides), NOW) == expected


def test_latest_play_subscription_takes_the_production_play_entry_expiring_last():
    info = _subscriber(
        nutree_monthly_149k=_entry(
            expires_date=_iso(NOW - 40 * DAY), product_plan_identifier="monthly"
        ),
        nutree_yearly_399k_no_trial=_entry(),
        nutree_weekly_99k=_entry(is_sandbox=True, expires_date=_iso(NOW + 900 * DAY)),
        nutree_weekly_49k=_entry(store="app_store", expires_date=_iso(NOW + 900 * DAY)),
    )

    play = script.latest_play_subscription(info, NOW)

    assert play == script.PlaySubscription(
        product_id="nutree_yearly_399k_no_trial:yearly",
        status="active",
        purchased_at=NOW - 30 * DAY,
        expires_at=NOW + 300 * DAY,
        cancelled_at=None,
        store_transaction_id="GPA.3374-2569-0717-66645",
    )


def test_latest_play_subscription_keeps_a_product_without_a_base_plan_as_is():
    info = _subscriber(legacy_monthly=_entry(product_plan_identifier=None))

    assert script.latest_play_subscription(info, NOW).product_id == "legacy_monthly"


@pytest.mark.parametrize(
    "info",
    [None, {}, _subscriber(), _subscriber(ios_yearly=_entry(store="app_store"))],
)
def test_latest_play_subscription_is_none_without_a_production_play_entry(info):
    assert script.latest_play_subscription(info, NOW) is None


def test_subscription_row_is_an_android_row_dated_from_the_first_purchase():
    play = script.latest_play_subscription(
        _subscriber(nutree_yearly_399k_no_trial=_entry()), NOW
    )

    row = script.subscription_row("user-1", "uid_a", play)

    assert row.user_id == "user-1"
    assert row.revenuecat_subscriber_id == "uid_a"
    assert row.platform == "android"
    assert row.status == "active"
    assert row.product_id == "nutree_yearly_399k_no_trial:yearly"
    assert row.purchased_at == row.created_at == NOW - 30 * DAY
    assert row.expires_at == NOW + 300 * DAY
    assert row.is_sandbox is False
    assert row.id


def test_queries_match_active_users_and_any_existing_row():
    user_sql = _sql(script.user_query("uid_a"))
    assert "users.firebase_uid = 'uid_a'" in user_sql
    assert "users.is_active IS true" in user_sql

    row_sql = _sql(script.existing_row_query("uid_a", "user-1"))
    assert "subscriptions.revenuecat_subscriber_id = 'uid_a'" in row_sql
    assert " OR subscriptions.user_id = 'user-1'" in row_sql


def _fakes(*, users, rows=(), subscribers=None, insert_result=True):
    inserted = []

    async def find_user(uid):
        return users.get(uid)

    async def row_exists(uid, user_id):
        return uid in rows

    async def fetch(uid):
        return (subscribers or {}).get(uid)

    async def insert(user_id, uid, play):
        inserted.append((user_id, uid, play.product_id))
        return insert_result

    return {
        "find_user": find_user,
        "row_exists": row_exists,
        "fetch": fetch,
        "insert": insert,
    }, inserted


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_run_inserts_only_missing_play_rows_and_only_with_apply(apply):
    fakes, inserted = _fakes(
        users={"uid_new": "user-1", "uid_row": "user-2", "uid_ios": "user-3"},
        rows={"uid_row"},
        subscribers={
            "uid_new": _subscriber(nutree_yearly_399k_no_trial=_entry()),
            "uid_ios": _subscriber(ios=_entry(store="app_store")),
        },
    )
    emitted = []

    counts = await script.run(
        ["uid_gone", "uid_row", "uid_ios", "uid_new"],
        apply=apply,
        emit=emitted.append,
        now=NOW,
        **fakes,
    )

    new_outcome = Outcome.INSERTED if apply else Outcome.PENDING
    assert [row["outcome"] for row in emitted] == [
        Outcome.NO_USER,
        Outcome.ROW_EXISTS,
        Outcome.NO_PLAY,
        new_outcome,
    ]
    assert emitted[-1]["status"] == "active"
    assert emitted[-1]["product_id"] == "nutree_yearly_399k_no_trial:yearly"
    assert counts["status:active"] == 1
    assert inserted == (
        [("user-1", "uid_new", "nutree_yearly_399k_no_trial:yearly")] if apply else []
    )


@pytest.mark.asyncio
async def test_run_leaves_a_row_written_by_a_webhook_during_the_run():
    fakes, inserted = _fakes(
        users={"uid_new": "user-1"},
        subscribers={"uid_new": _subscriber(nutree_weekly_99k=_entry())},
        insert_result=False,
    )
    emitted = []

    counts = await script.run(
        ["uid_new"], apply=True, emit=emitted.append, now=NOW, **fakes
    )

    assert emitted[0]["outcome"] == Outcome.ROW_EXISTS
    assert counts["status:active"] == 0
    assert len(inserted) == 1


@pytest.mark.asyncio
async def test_run_records_each_error_and_stops_after_consecutive_failures():
    async def failing_fetch(uid):
        raise RuntimeError("revenuecat unavailable")

    fakes, inserted = _fakes(users={f"uid_{n}": f"user-{n}" for n in range(5)})
    fakes["fetch"] = failing_fetch
    emitted = []

    counts = await script.run(
        [f"uid_{n}" for n in range(5)],
        apply=True,
        emit=emitted.append,
        now=NOW,
        **fakes,
    )

    assert counts[Outcome.ERROR] == script.MAX_CONSECUTIVE_ERRORS
    assert emitted[-1] == {"stopped": "3 consecutive errors"}
    assert "revenuecat unavailable" in emitted[0]["error"]
    assert inserted == []
