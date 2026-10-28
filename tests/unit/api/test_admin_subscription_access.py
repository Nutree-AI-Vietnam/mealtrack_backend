"""Support API for subscription access (/v1/admin/subscription-access/*).

The routes run with their real query and command handlers; only the
repository (SQL, covered on PostgreSQL in
tests/integration/postgres/test_subscription_access_support_postgres.py) and
the RevenueCat refresher are faked.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.dependencies import auth as auth_dep
from src.api.dependencies.auth import require_admin
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1 import admin_subscription_access as route_mod
from src.app.commands.subscription_access import ResyncSubscriptionAccessCommand
from src.app.handlers.command_handlers.resync_subscription_access_command_handler import (
    ResyncSubscriptionAccessCommandHandler,
)
from src.app.handlers.query_handlers.get_support_subscription_access_query_handler import (
    GetSupportSubscriptionAccessQueryHandler,
)
from src.app.handlers.query_handlers.list_unresolved_subscription_access_query_handler import (
    ListUnresolvedSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import (
    GetSupportSubscriptionAccessQuery,
    ListUnresolvedSubscriptionAccessQuery,
)
from src.domain.ports.subscription_access_refresh_port import (
    ProviderAccessPeek,
    ProviderEntitlement,
    SubscriptionAccessApplyError,
    SubscriptionAccessRefresh,
    SubscriptionAccessRefreshPort,
)
from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
    SupportSubscriptionRecord,
    SupportUserRecord,
    UnresolvedAccessUser,
    UnresolvedQueueCursor,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable

NOW = datetime.now(UTC).replace(microsecond=0)
ADMIN = "support@nutree.test"
BASE = "/v1/admin/subscription-access"

USER = SupportUserRecord(
    id="db-user-1",
    firebase_uid="firebase-uid-1",
    email="buyer@example.com",
    is_active=True,
    deleted_at=None,
)


def _row(**values) -> SupportSubscriptionRecord:
    facts = {
        "id": "row-1",
        "provider": "revenuecat",
        "status": "active",
        "product_id": "standard_annual",
        "platform": "ios",
        "purchased_at": NOW - timedelta(days=30),
        "expires_at": NOW + timedelta(days=335),
        "grace_period_expires_at": None,
        "period_type": "normal",
        "provider_snapshot_at": NOW - timedelta(days=1),
        "provider_sync_required_at": None,
        "provider_refund_pending_at": None,
        "provider_transfer_source_at": None,
        "is_sandbox": False,
    }
    return SupportSubscriptionRecord(**(facts | values))


class _Repository(SubscriptionAccessSupportRepositoryPort):
    """In-memory stand-in; it records every call so tests can tell reads apart."""

    def __init__(self, users=(USER,), rows=None, unresolved=()):
        self.users = list(users)
        self.rows = {user.id: list((rows or {}).get(user.id, [])) for user in users}
        self.unresolved = list(unresolved)
        self.calls: list[tuple] = []
        self.matches: list[SupportUserRecord] | None = None

    async def find_users(self, query, *, limit):
        self.calls.append(("find_users", query, limit))
        if self.matches is not None:
            return self.matches
        return [u for u in self.users if query in (u.id, u.firebase_uid, u.email)]

    async def get_user(self, user_id):
        self.calls.append(("get_user", user_id))
        return next((u for u in self.users if u.id == user_id), None)

    async def subscription_rows(self, user_id):
        self.calls.append(("subscription_rows", user_id))
        return list(self.rows.get(user_id, []))

    async def list_unresolved(self, *, limit, after):
        self.calls.append(("list_unresolved", limit, after))
        ordered = sorted(self.unresolved, key=lambda u: (u.oldest_marker_at, u.user_id))
        if after is not None:
            ordered = [
                u
                for u in ordered
                if (u.oldest_marker_at, u.user_id)
                > (after.oldest_marker_at, after.user_id)
            ]
        return ordered[:limit]


class _Refresher(SubscriptionAccessRefreshPort):
    def __init__(self, repository: _Repository, *, refresh=None, peek=None, after=None):
        self.repository = repository
        self.refresh_answer = refresh or SubscriptionAccessRefresh(
            "applied", markers_cleared=1
        )
        self.peek_answer = peek
        self.after = after  # rows a successful refresh leaves behind
        self.calls: list[tuple] = []

    async def refresh(self, user_id, *, source="refresh"):
        self.calls.append(("refresh", user_id, source))
        if isinstance(self.refresh_answer, BaseException):
            raise self.refresh_answer
        if self.after is not None:
            self.repository.rows[user_id] = self.after
        return self.refresh_answer

    async def peek(self, user_id, *, source):
        self.calls.append(("peek", user_id, source))
        if isinstance(self.peek_answer, BaseException):
            raise self.peek_answer
        return self.peek_answer


class _Bus:
    """Dispatches to the real handlers, like the configured event bus."""

    def __init__(self, repository: _Repository, refresher: _Refresher):
        self.handlers = {
            GetSupportSubscriptionAccessQuery: GetSupportSubscriptionAccessQueryHandler(
                repository, refresher
            ),
            ResyncSubscriptionAccessCommand: ResyncSubscriptionAccessCommandHandler(
                repository, refresher
            ),
            ListUnresolvedSubscriptionAccessQuery: (
                ListUnresolvedSubscriptionAccessQueryHandler(repository)
            ),
        }
        self.sent: list = []

    async def send(self, message):
        self.sent.append(message)
        return await self.handlers[type(message)].handle(message)


def _token(uid: str, email: str | None = ADMIN) -> str:
    """An unsigned JWT: the limiter keys on its uid; verification is stubbed."""

    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"sub": uid, "uid": uid}
    if email is not None:
        claims["email"] = email
    return f"{part({'alg': 'none'})}.{part(claims)}.signature"


def _client(bus, *, real_auth: bool = False) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(route_mod.router)
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    if not real_auth:
        app.dependency_overrides[require_admin] = lambda: ADMIN
    return TestClient(app)


def _headers(uid: str = "admin-uid", email: str | None = ADMIN) -> dict:
    return {"Authorization": f"Bearer {_token(uid, email)}"}


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter.clear_memory()
    yield
    limiter.clear_memory()


@pytest.fixture(autouse=True)
def forgotten(monkeypatch):
    forget = AsyncMock()
    monkeypatch.setattr(route_mod, "forget_revenuecat_check", forget)
    return forget


@pytest.fixture
def metric(monkeypatch):
    recorded = MagicMock()
    monkeypatch.setattr(route_mod, "increment_metric", recorded)
    return recorded


@pytest.fixture
def real_tokens(monkeypatch):
    """Verify bearer tokens by decoding them, so require_admin runs for real."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(
        auth_dep.settings, "ADMIN_EMAILS", f"{ADMIN}, other@nutree.test"
    )

    async def verify(token, *, check_revoked=False):
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    monkeypatch.setattr(auth_dep, "_verify_id_token", verify)


def _setup(rows=None, **refresher):
    repository = _Repository(rows=rows)
    refresh = _Refresher(repository, **refresher)
    return repository, refresh, _Bus(repository, refresh)


# --- Auth ---------------------------------------------------------------------


ROUTES = [
    ("GET", f"{BASE}/users?query=buyer@example.com"),
    ("POST", f"{BASE}/users/db-user-1/resync"),
    ("GET", f"{BASE}/unresolved"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_needs_a_firebase_token(real_tokens, method, path):
    repository, refresher, bus = _setup()

    response = _client(bus, real_auth=True).request(method, path)

    assert response.status_code == 401
    assert bus.sent == [] and refresher.calls == [] and repository.calls == []


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("email", ["buyer@example.com", None])
def test_every_route_refuses_a_non_admin(real_tokens, method, path, email):
    repository, refresher, bus = _setup()

    response = _client(bus, real_auth=True).request(
        method, path, headers=_headers(uid="customer-uid", email=email)
    )

    assert response.status_code == 403
    assert bus.sent == [] and refresher.calls == [] and repository.calls == []


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_serves_an_admin(real_tokens, method, path):
    _, _, bus = _setup(rows={USER.id: [_row()]})

    response = _client(bus, real_auth=True).request(
        method, path, headers=_headers(email="Support@Nutree.test")
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


# --- Lookup -------------------------------------------------------------------


@pytest.mark.parametrize("query", ["buyer@example.com", "firebase-uid-1", "db-user-1"])
def test_lookup_by_email_uid_or_id_returns_rows_decision_and_flags(monkeypatch, query):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIPTION_ENFORCEMENT_ENABLED", "false")
    momo = _row(
        id="row-2",
        provider="momo",
        product_id="momo_pass_90d",
        platform="web",
        expires_at=NOW + timedelta(days=10),
    )
    repository, refresher, bus = _setup(rows={USER.id: [_row(), momo]})

    response = _client(bus).get(
        f"{BASE}/users", params={"query": query}, headers=_headers()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": "db-user-1",
        "firebase_uid": "firebase-uid-1",
        "email": "buyer@example.com",
        "is_active": True,
        "deleted_at": None,
    }
    assert [row["provider"] for row in body["subscriptions"]] == ["revenuecat", "momo"]
    assert set(body["subscriptions"][0]) == {
        "id",
        "provider",
        "status",
        "product_id",
        "platform",
        "purchased_at",
        "expires_at",
        "grace_period_expires_at",
        "provider_snapshot_at",
        "provider_sync_required_at",
        "provider_refund_pending_at",
        "provider_transfer_source_at",
        "is_sandbox",
    }
    assert body["decision"] == {
        "outcome": "granted",
        "has_access": True,
        "status": "active",
        "expires_at": body["subscriptions"][0]["expires_at"],
        "product_id": "standard_annual",
        "will_renew": True,
        "provider": "revenuecat",
        "unresolved_reasons": [],
    }
    assert body["flags"] == {
        "subscription_access_enabled": True,
        "subscription_enforcement_enabled": False,
        "enforcement_active": False,
    }
    assert body["live"] is None
    # A plain lookup never calls RevenueCat (GET /subscribers is get-or-create).
    assert refresher.calls == []
    assert repository.calls[0] == ("find_users", query, 5)


def test_lookup_explains_an_unresolved_decision():
    marked = _row(
        status="cancelled",
        provider_sync_required_at=NOW - timedelta(hours=2),
        provider_refund_pending_at=NOW - timedelta(hours=2),
    )
    _, _, bus = _setup(rows={USER.id: [marked]})

    body = _client(bus).get(f"{BASE}/users", params={"query": "db-user-1"}).json()

    assert body["decision"] == {
        "outcome": "unresolved",
        "has_access": None,
        "status": None,
        "expires_at": None,
        "product_id": None,
        "will_renew": None,
        "provider": None,
        "unresolved_reasons": ["sync_required", "refund_pending"],
    }


def test_lookup_of_nobody_is_not_found(caplog):
    _, _, bus = _setup()

    with caplog.at_level(logging.INFO, logger=route_mod.logger.name):
        response = _client(bus).get(f"{BASE}/users", params={"query": "who@x.test"})

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "USER_NOT_FOUND"
    # The audit line names the admin, never the customer's address.
    (line,) = [r.getMessage() for r in caplog.records]
    assert f"admin={ADMIN} action=lookup" in line and "result=not_found" in line
    assert "who@x.test" not in caplog.text


def test_lookup_matching_several_accounts_lists_their_ids():
    repository, _, bus = _setup()
    repository.matches = [
        USER,
        replace(USER, id="db-user-2", email="Buyer@example.com"),
    ]

    response = _client(bus).get(f"{BASE}/users", params={"query": "BUYER@example.com"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "AMBIGUOUS_QUERY"
    assert detail["details"] == {"user_ids": ["db-user-1", "db-user-2"]}


def test_lookup_rejects_an_empty_query():
    _, _, bus = _setup()

    assert _client(bus).get(f"{BASE}/users", params={"query": ""}).status_code == 422


# --- Live read ----------------------------------------------------------------


PEEK = ProviderAccessPeek(
    read_at=NOW,
    absent=False,
    entitlements=("standard",),
    standard=ProviderEntitlement(
        has_access=True,
        status="cancelled",
        product_id="standard_annual",
        platform="ios",
        purchased_at=NOW - timedelta(days=30),
        expires_at=NOW + timedelta(days=335),
        grace_period_expires_at=None,
        period_type="normal",
        is_sandbox=False,
        will_renew=False,
    ),
)


def test_live_lookup_reads_revenuecat_and_writes_nothing():
    repository, refresher, bus = _setup(rows={USER.id: [_row()]}, peek=PEEK)

    body = (
        _client(bus)
        .get(f"{BASE}/users", params={"query": "db-user-1", "live": "true"})
        .json()
    )

    assert refresher.calls == [("peek", "db-user-1", "support_lookup")]
    assert body["live"]["status"] == "ok"
    assert body["live"]["entitlements"] == ["standard"]
    assert body["live"]["standard_missing"] is False
    assert body["live"]["standard"]["status"] == "cancelled"
    assert body["live"]["standard"]["will_renew"] is False
    # Read-only: no refresh (the only writer) ran, the rows are as they were.
    assert all(call[0] != "refresh" for call in refresher.calls)
    assert body["subscriptions"][0]["provider_snapshot_at"] is not None
    assert [call[0] for call in repository.calls] == ["find_users", "subscription_rows"]


def test_live_lookup_while_revenuecat_is_down_still_shows_the_rows():
    down = RevenueCatSnapshotUnavailable(
        "RevenueCat returned HTTP 503", retry_after_seconds=30, reason="http_5xx"
    )
    _, _, bus = _setup(rows={USER.id: [_row()]}, peek=down)

    response = _client(bus).get(
        f"{BASE}/users", params={"query": "db-user-1", "live": "true"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["outcome"] == "granted"
    assert body["live"] == {
        "status": "unavailable",
        "reason": "http_5xx",
        "retry_after_seconds": 30.0,
        "read_at": None,
        "absent": None,
        "entitlements": [],
        "standard_missing": False,
        "standard": None,
    }


def test_live_lookup_never_reads_a_deleted_account():
    """Its uid is a tombstone: reading it would create a RevenueCat customer."""
    deleted = replace(USER, is_active=False, deleted_at=NOW)
    repository = _Repository(users=[deleted])
    refresher = _Refresher(repository, peek=PEEK)

    body = (
        _client(_Bus(repository, refresher))
        .get(f"{BASE}/users", params={"query": "db-user-1", "live": "true"})
        .json()
    )

    assert body["live"]["status"] == "not_read"
    assert body["live"]["reason"] == "user_inactive"
    assert refresher.calls == []


# --- Resync -------------------------------------------------------------------


def test_resync_applies_revenuecat_and_answers_before_and_after(
    metric, forgotten, caplog
):
    stale = _row(status="cancelled", provider_sync_required_at=NOW - timedelta(hours=1))
    renewed = _row(provider_snapshot_at=NOW)
    repository, refresher, bus = _setup(
        rows={USER.id: [stale]},
        refresh=SubscriptionAccessRefresh("applied", markers_cleared=1),
        after=[renewed],
    )

    with caplog.at_level(logging.INFO, logger=route_mod.logger.name):
        response = _client(bus).post(
            f"{BASE}/users/db-user-1/resync", headers=_headers()
        )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "db-user-1"
    assert body["sync"] == {
        "result": "applied",
        "reason": None,
        "rows_revoked": 0,
        "row_created": False,
        "markers_cleared": 1,
        "standard_missing": False,
    }
    assert body["before"]["outcome"] == "unresolved"
    assert body["before"]["unresolved_reasons"] == ["sync_required"]
    assert body["after"]["outcome"] == "granted"
    assert refresher.calls == [("refresh", "db-user-1", "support_resync")]
    forgotten.assert_awaited_once_with("db-user-1")
    # Audit: the log names the admin; the metric never carries an email.
    metric.assert_called_once_with(
        "subscription_access.support_action",
        attributes={"operation": "resync", "result": "applied", "user_id": "db-user-1"},
    )
    (line,) = [r.getMessage() for r in caplog.records]
    assert f"admin={ADMIN} action=resync target_user_id=db-user-1" in line
    assert "result=applied" in line and "before=unresolved after=granted" in line


def test_resync_while_revenuecat_is_down_answers_503_with_the_reason(metric, forgotten):
    down = RevenueCatSnapshotUnavailable(
        "RevenueCat returned HTTP 429", retry_after_seconds=12.5, reason="http_429"
    )
    repository, _, bus = _setup(rows={USER.id: [_row()]}, refresh=down)

    response = _client(bus).post(f"{BASE}/users/db-user-1/resync")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "13"
    assert response.headers["cache-control"] == "private, no-store"
    detail = response.json()["detail"]
    assert detail["error_code"] == "REVENUECAT_UNAVAILABLE"
    assert detail["details"] == {"reason": "http_429", "retry_after_seconds": 12.5}
    forgotten.assert_not_awaited()
    assert metric.call_args.kwargs["attributes"]["result"] == "revenuecat_unavailable"
    assert repository.rows[USER.id] == [_row()]


def test_resync_of_a_transfer_source_applies_the_emptied_customer():
    """The rows remember the TRANSFER; the sync applies RevenueCat's empty
    customer as authoritative (as reconcile --repair does), so the resync
    resolves the user instead of answering absent_with_history."""
    moved = _row(
        provider_sync_required_at=NOW - timedelta(hours=3),
        provider_transfer_source_at=NOW - timedelta(hours=3),
    )
    revoked = _row(status="expired", provider_snapshot_at=NOW)
    _, refresher, bus = _setup(
        rows={USER.id: [moved]},
        refresh=SubscriptionAccessRefresh("applied", rows_revoked=1, markers_cleared=1),
        after=[revoked],
    )

    body = _client(bus).post(f"{BASE}/users/db-user-1/resync").json()

    assert body["before"]["unresolved_reasons"] == ["sync_required", "transfer_source"]
    assert (body["sync"]["result"], body["sync"]["rows_revoked"]) == ("applied", 1)
    assert body["after"]["outcome"] == "denied"
    # Same read and sync as the user's own refresh, labelled as support's.
    assert refresher.calls == [("refresh", "db-user-1", "support_resync")]


def test_resync_that_cannot_be_applied_says_why():
    _, _, bus = _setup(
        rows={USER.id: [_row()]},
        refresh=SubscriptionAccessRefresh("not_applied", reason="absent_with_history"),
    )

    body = _client(bus).post(f"{BASE}/users/db-user-1/resync").json()

    assert (body["sync"]["result"], body["sync"]["reason"]) == (
        "not_applied",
        "absent_with_history",
    )


def test_resync_apply_failure_is_answered_without_a_second_report(
    monkeypatch, metric, forgotten
):
    capture = MagicMock()
    monkeypatch.setattr("src.observability.capture_exception", capture)
    failure = SubscriptionAccessApplyError("RevenueCat snapshot could not be applied")
    _, _, bus = _setup(rows={USER.id: [_row()]}, refresh=failure)

    response = _client(bus).post(f"{BASE}/users/db-user-1/resync")

    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == "RESYNC_FAILED"
    capture.assert_not_called()
    forgotten.assert_not_awaited()
    assert metric.call_args.kwargs["attributes"]["result"] == "apply_failed"


@pytest.mark.parametrize(
    ("user", "reason"),
    [
        (replace(USER, is_active=False, deleted_at=NOW), "user_inactive"),
        (replace(USER, firebase_uid=None), "no_firebase_uid"),
    ],
)
def test_resync_never_reads_an_account_revenuecat_cannot_know(user, reason):
    repository = _Repository(users=[user])
    refresher = _Refresher(repository)

    response = _client(_Bus(repository, refresher)).post(
        f"{BASE}/users/db-user-1/resync"
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["error_code"], detail["details"]) == (
        "USER_INACTIVE",
        {"reason": reason},
    )
    assert refresher.calls == []


def test_resync_of_an_unknown_user_is_not_found(metric):
    repository, refresher, bus = _setup()

    response = _client(bus).post(f"{BASE}/users/nobody/resync")

    assert response.status_code == 404
    assert refresher.calls == []
    assert metric.call_args.kwargs["attributes"]["result"] == "user_not_found"


def test_resync_is_rate_limited_per_admin():
    _, refresher, bus = _setup(rows={USER.id: [_row()]})
    client = _client(bus)

    statuses = [
        client.post(f"{BASE}/users/db-user-1/resync", headers=_headers()).status_code
        for _ in range(10)
    ]
    limited = client.post(f"{BASE}/users/db-user-1/resync", headers=_headers())
    other_admin = client.post(
        f"{BASE}/users/db-user-1/resync", headers=_headers(uid="admin-uid-2")
    )

    assert statuses == [200] * 10
    assert limited.status_code == 429
    assert other_admin.status_code == 200
    assert len(refresher.calls) == 11  # the limited call never reached RevenueCat


# --- Repair queue -------------------------------------------------------------


def _marked(index: int, **values) -> UnresolvedAccessUser:
    marked_at = NOW - timedelta(hours=10 - index)
    facts = {
        "user_id": f"db-user-{index:02d}",
        "firebase_uid": f"uid-{index:02d}",
        "oldest_marker_at": marked_at,
        "sync_required_at": marked_at,
        "refund_pending_at": None,
        "transfer_source_at": None,
    }
    return UnresolvedAccessUser(**(facts | values))


def test_queue_pages_oldest_first_with_an_opaque_cursor():
    queue = [_marked(index) for index in range(5)]
    queue[1] = _marked(
        1, refund_pending_at=queue[1].oldest_marker_at + timedelta(minutes=5)
    )
    repository = _Repository(unresolved=queue)
    client = _client(_Bus(repository, _Refresher(repository)))

    first = client.get(f"{BASE}/unresolved", params={"limit": 2}).json()
    second = client.get(
        f"{BASE}/unresolved", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    third = client.get(
        f"{BASE}/unresolved", params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()

    pages = [first, second, third]
    assert [[item["user_id"] for item in page["items"]] for page in pages] == [
        ["db-user-00", "db-user-01"],
        ["db-user-02", "db-user-03"],
        ["db-user-04"],
    ]
    assert third["next_cursor"] is None
    item = first["items"][1]
    assert item["firebase_uid"] == "uid-01"
    assert [marker["type"] for marker in item["markers"]] == [
        "sync_required",
        "refund_pending",
    ]
    assert 9 * 3600 - 60 <= item["oldest_marker_age_seconds"] <= 9 * 3600 + 60
    assert item["markers"][1]["age_seconds"] == item["oldest_marker_age_seconds"] - 300
    # Each page asks for one row more than it shows, to know if more follow.
    assert repository.calls[0] == ("list_unresolved", 3, None)
    assert repository.calls[1] == (
        "list_unresolved",
        3,
        UnresolvedQueueCursor(queue[1].oldest_marker_at, "db-user-01"),
    )


def test_queue_with_nothing_marked_is_empty():
    _, _, bus = _setup()

    body = _client(bus).get(f"{BASE}/unresolved").json()

    assert (body["items"], body["next_cursor"], body["limit"]) == ([], None, 50)


@pytest.mark.parametrize("cursor", ["not-a-cursor", "W10", "WyJ4IiwgMV0"])
def test_queue_rejects_a_cursor_it_did_not_hand_out(cursor):
    _, _, bus = _setup()

    response = _client(bus).get(f"{BASE}/unresolved", params={"cursor": cursor})

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_CURSOR"
    assert bus.sent == []


@pytest.mark.parametrize("limit", [0, 201])
def test_queue_limit_is_bounded(limit):
    _, _, bus = _setup()

    assert (
        _client(bus).get(f"{BASE}/unresolved", params={"limit": limit}).status_code
        == 422
    )
