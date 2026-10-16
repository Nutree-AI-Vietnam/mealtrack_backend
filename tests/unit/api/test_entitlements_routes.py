"""/v1/me/entitlements and /v1/admin/entitlements routes (NM-680)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
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

from src.api.base_dependencies import (
    get_create_entitlement_grant_handler,
    get_entitlement_account_service,
    get_entitlement_query_service,
    get_revoke_entitlement_grant_handler,
)
from src.api.dependencies import auth as auth_dep
from src.api.dependencies.auth import get_current_user_id, verify_firebase_token
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1.entitlements import (
    admin_router,
    entitlements_response,
    etag_matches,
    router,
)
from src.app.handlers.command_handlers.entitlements import (
    CreateEntitlementGrantCommandHandler,
    RevokeEntitlementGrantCommandHandler,
)
from src.app.schemas.entitlement_schemas import EntitlementsView
from src.app.services.entitlement_account_service import (
    HINTED_SYNC_LIMIT,
    SYNC_RATE_LIMIT,
    DelayedResyncScheduler,
    EntitlementAccountService,
    SlidingWindowRateLimiter,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import FLAG_ENT_READ_API
from src.domain.model.billing.ledger import UserEntitlement

U1, UID1 = "user-1", "uid-1"
ACCESS_UNTIL = datetime(2026, 11, 1, tzinfo=UTC)
SYNCED_AT = datetime(2026, 10, 15, 7, 59, 58, tzinfo=UTC)

# The exact body from the NM-680 spec, shared with the Flutter Freezed DTO.
CONTRACT_EXAMPLE = {
    "schema_version": 1,
    "authority": "backend",
    "version": 42,
    "server_time": "2026-10-15T08:00:00Z",
    "has_access": True,
    "state": "cancelled_active",
    "ever_subscribed": True,
    "entitlements": {
        "standard": {
            "active": True,
            "state": "cancelled_active",
            "access_until": "2026-11-01T00:00:00Z",
            "will_renew": False,
            "period_type": "normal",
            "product_id": "nutree_2999_1y_0",
            "store": "app_store",
            "source": "store",
            "is_sandbox": False,
        }
    },
    "synced_at": "2026-10-15T07:59:58Z",
    "stale": False,
    "sync_error": None,
    "ttl_seconds": 300,
}


class _Scheduler(DelayedResyncScheduler):
    def __init__(self):
        super().__init__(lambda _user_id: asyncio.sleep(0))
        self.scheduled = []

    def schedule(self, user_id):
        self.scheduled.append(user_id)


class _Env:
    def __init__(self):
        self.uow = FakeUnitOfWork()
        self.uow.users.users[U1] = make_user(U1, UID1)
        self.clock = FakeClock(NOW)
        self.provider = FakeBillingProvider()
        self.provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
        self.flags = StaticFeatureFlagReader({FLAG_ENT_READ_API: True})
        self.scheduler = _Scheduler()
        self.sync = EntitlementSyncService(
            uow_factory=self.factory,
            provider=self.provider,
            budget=FakeBudget(),
            clock=self.clock,
        )
        self.query = EntitlementQueryService(
            uow_factory=self.factory,
            sync_service=self.sync,
            flags=self.flags,
            clock=self.clock,
        )
        self.service = EntitlementAccountService(
            query_service=self.query,
            sync_service=self.sync,
            inbox_service=EntitlementInboxService(
                uow_factory=self.factory, sync_service=self.sync, clock=self.clock
            ),
            flags=self.flags,
            scheduler=self.scheduler,
            rate_limiter=SlidingWindowRateLimiter(),
            hint_rate_limiter=SlidingWindowRateLimiter(limit=HINTED_SYNC_LIMIT),
            clock=self.clock,
        )
        self.token = {
            "uid": "admin-uid",
            "email": "admin@nutree.ai",
            "email_verified": True,
            "firebase": {"sign_in_provider": "google.com"},
        }

    def factory(self):
        return self.uow

    def seed_contract_projection(self):
        self.uow.user_entitlements.rows[(U1, "standard")] = UserEntitlement(
            user_id=U1,
            entitlement_key="standard",
            has_access=True,
            state="cancelled_active",
            primary_source="store",
            primary_ref="rc:uid-1:nutree_2999_1y_0",
            version=42,
            computed_at=SYNCED_AT,
            access_until=ACCESS_UNTIL,
            next_change_at=ACCESS_UNTIL,
            will_renew=False,
            period_type="normal",
            product_id="nutree_2999_1y_0",
            store="app_store",
            ever_had_access=True,
            last_provider_sync_at=SYNCED_AT,
        )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(auth_dep.settings, "ADMIN_EMAILS", "admin@nutree.ai")
    return _Env()


@pytest.fixture
def client(env):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    register_exception_handlers(app)
    app.include_router(router)
    app.include_router(admin_router)
    app.dependency_overrides[get_current_user_id] = lambda: U1
    app.dependency_overrides[get_entitlement_account_service] = lambda: env.service
    app.dependency_overrides[get_entitlement_query_service] = lambda: env.query
    app.dependency_overrides[verify_firebase_token] = lambda: env.token
    app.dependency_overrides[get_create_entitlement_grant_handler] = lambda: (
        CreateEntitlementGrantCommandHandler(
            uow_factory=env.factory, sync_service=env.sync, clock=env.clock
        )
    )
    app.dependency_overrides[get_revoke_entitlement_grant_handler] = lambda: (
        RevokeEntitlementGrantCommandHandler(
            uow_factory=env.factory, sync_service=env.sync, clock=env.clock
        )
    )
    limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
    limiter.reset()


# ---- contract ----


def test_contract_snapshot_matches_the_spec_exactly(client, env):
    env.seed_contract_projection()

    response = client.get("/v1/me/entitlements")

    assert response.status_code == 200
    assert response.json() == CONTRACT_EXAMPLE
    assert list(response.json()) == list(CONTRACT_EXAMPLE)
    assert response.headers["cache-control"] == "private, no-cache"
    epoch = int(ACCESS_UNTIL.timestamp())
    assert response.headers["etag"] == f'W/"{U1}:42:{epoch}"'


def test_flags_off_answers_rc_authority(client, env):
    env.flags.set(FLAG_ENT_READ_API, False)
    env.seed_contract_projection()

    get = client.get("/v1/me/entitlements")
    post = client.post(
        "/v1/me/entitlements/sync", json={"client_hint": {"kind": "purchase"}}
    )

    for response in (get, post):
        body = response.json()
        assert response.status_code == 200
        assert body["authority"] == "rc"
        assert body["state"] == "unknown"
        assert body["has_access"] is False
        assert body["entitlements"] == {}
    assert env.provider.calls == []


def test_never_synced_user_reads_unknown_with_backend_authority(client):
    body = client.get("/v1/me/entitlements").json()

    assert body["authority"] == "backend"
    assert body["state"] == "unknown"
    assert body["synced_at"] is None


# ---- ETag / 304 ----


def test_if_none_match_answers_304_while_nothing_changes(client, env):
    env.seed_contract_projection()
    etag = client.get("/v1/me/entitlements").headers["etag"]

    response = client.get("/v1/me/entitlements", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


def test_stale_etag_gets_a_full_body(client, env):
    env.seed_contract_projection()

    response = client.get(
        "/v1/me/entitlements", headers={"If-None-Match": f'W/"{U1}:41:0"'}
    )

    assert response.status_code == 200


def test_no_304_once_next_change_at_has_passed(client, env):
    env.seed_contract_projection()
    etag = client.get("/v1/me/entitlements").headers["etag"]
    env.clock.now = ACCESS_UNTIL + timedelta(seconds=1)

    response = client.get("/v1/me/entitlements", headers={"If-None-Match": etag})

    assert response.status_code == 200
    assert response.json()["has_access"] is False
    assert response.json()["state"] == "expired"


def test_304_rule_checks_next_change_even_for_a_matching_etag():
    view = EntitlementsView(
        user_id=U1,
        authority="backend",
        version=3,
        server_time=NOW,
        has_access=True,
        state="active",
        ever_subscribed=True,
        next_change_at=NOW,
    )

    response = entitlements_response(view, if_none_match=view.etag, now=NOW)

    assert response.status_code == 200


def test_etag_matching_is_weak_and_list_aware():
    assert etag_matches('"a", W/"b"', 'W/"b"')
    assert etag_matches('"b"', 'W/"b"')
    assert etag_matches("*", 'W/"b"')
    assert not etag_matches('W/"c"', 'W/"b"')


# ---- POST /sync ----


def test_hinted_sync_fetches_and_schedules_follow_ups(client, env):
    response = client.post(
        "/v1/me/entitlements/sync",
        json={
            "client_hint": {
                "kind": "purchase",
                "product_id": "nutree_2999_1y_0",
                "transaction_id": "1000",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["has_access"] is True
    assert env.provider.calls == [UID1]
    assert env.scheduler.scheduled == [U1]
    assert "etag" in response.headers


def test_hinted_sync_never_caches_a_negative(client, env):
    env.provider.responses[UID1] = snapshot(UID1)

    response = client.post(
        "/v1/me/entitlements/sync", json={"client_hint": {"kind": "purchase"}}
    )

    assert response.json()["has_access"] is False
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    # A GET inside the hint window is not cacheable either.
    get = client.get("/v1/me/entitlements", headers={"If-None-Match": "*"})
    assert get.status_code == 200
    assert get.headers["cache-control"] == "no-store"
    assert "etag" not in get.headers
    # After the window a "no" is an ordinary cacheable answer.
    env.clock.advance(minutes=16)
    assert "etag" in client.get("/v1/me/entitlements").headers


def test_unhinted_sync_without_body(client, env):
    response = client.post("/v1/me/entitlements/sync")

    assert response.status_code == 200
    assert env.provider.calls == [UID1]
    assert env.scheduler.scheduled == []


def test_unknown_hint_kind_is_treated_as_unhinted(client, env):
    client.post("/v1/me/entitlements/sync")
    env.clock.advance(minutes=1)

    client.post(
        "/v1/me/entitlements/sync", json={"client_hint": {"kind": "gift"}, "x": 1}
    )

    assert env.provider.calls == [UID1]  # fresh projection, no second fetch


def test_provider_unavailable_is_200_stale_with_last_known_state(client, env):
    client.post("/v1/me/entitlements/sync")
    env.provider.responses[UID1] = ProviderUnavailable("down")

    response = client.post(
        "/v1/me/entitlements/sync", json={"client_hint": {"kind": "restore"}}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["has_access"] is True
    assert body["stale"] is True
    assert body["sync_error"] == "provider_unavailable"
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


def test_soft_rate_limit_answers_from_the_projection(client, env):
    budget = HINTED_SYNC_LIMIT + SYNC_RATE_LIMIT
    for _ in range(budget + 1):
        response = client.post(
            "/v1/me/entitlements/sync", json={"client_hint": {"kind": "purchase"}}
        )
        assert response.status_code == 200

    assert len(env.provider.calls) == budget


# ---- admin ----


def _grant_body(**overrides):
    body = {
        "user_id": U1,
        "source": "support",
        "reason": "refund goodwill",
        "ends_at": (NOW + timedelta(days=30)).isoformat(),
    }
    body.update(overrides)
    return body


def test_admin_grant_create_revoke_and_debug_view(client, env):
    created = client.post(
        "/v1/admin/entitlements/grants",
        json=_grant_body(starts_at=NOW.isoformat()),
    )
    assert created.status_code == 201, created.text
    grant = created.json()["grant"]
    assert grant["created_by"] == "admin@nutree.ai"
    assert created.json()["projection"]["has_access"] is True
    assert created.json()["projection"]["primary_source"] == "grant"

    debug = client.get(f"/v1/admin/entitlements/{U1}")
    assert debug.status_code == 200
    assert debug.json()["grants"][0]["id"] == grant["id"]
    assert debug.json()["entitlements"]["entitlements"]["standard"]["source"] == "grant"

    revoked = client.post(
        f"/v1/admin/entitlements/grants/{grant['id']}/revoke",
        json={"reason": "issued by mistake"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["grant"]["revoke_reason"] == "issued by mistake"
    assert revoked.json()["grant"]["revoked_by"] == "admin@nutree.ai"
    assert revoked.json()["projection"]["has_access"] is False
    debug = client.get(f"/v1/admin/entitlements/{U1}")
    assert debug.json()["grants"][0]["revoked_by"] == "admin@nutree.ai"


def test_admin_grant_is_idempotent_on_source_ref(client, env):
    body = _grant_body(source_ref="ticket-1", starts_at=NOW.isoformat())

    first = client.post("/v1/admin/entitlements/grants", json=body)
    second = client.post("/v1/admin/entitlements/grants", json=body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert second.json()["grant"]["id"] == first.json()["grant"]["id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "ends_at": (NOW + timedelta(days=400)).isoformat(),
            "starts_at": NOW.isoformat(),
        },
        {"reason": "   "},
        {"ends_at": "2026-11-01T00:00:00"},
    ],
)
def test_admin_grant_rejects_d5_violations(client, overrides):
    response = client.post(
        "/v1/admin/entitlements/grants", json=_grant_body(**overrides)
    )

    assert response.status_code == 400


def test_admin_grant_rejects_unknown_source(client):
    response = client.post(
        "/v1/admin/entitlements/grants", json=_grant_body(source="lifetime")
    )
    assert response.status_code == 422


def test_admin_grant_for_unknown_user_is_404(client):
    response = client.post(
        "/v1/admin/entitlements/grants", json=_grant_body(user_id="nobody")
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "token_change",
    [
        {"email_verified": False},
        {"firebase": {"sign_in_provider": "custom"}},
        {"email": "someone@else.com"},
    ],
)
def test_admin_routes_require_hardened_admin(client, env, token_change):
    env.token.update(token_change)

    responses = [
        client.post("/v1/admin/entitlements/grants", json=_grant_body()),
        client.post("/v1/admin/entitlements/grants/g-1/revoke", json={"reason": "x"}),
        client.get(f"/v1/admin/entitlements/{U1}"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert env.uow.entitlement_grants.rows == {}


def test_debug_view_hides_raw_payloads(client, env):
    client.post("/v1/me/entitlements/sync")
    body = client.get(f"/v1/admin/entitlements/{U1}").json()

    assert body["store_subscriptions"][0]["product_id"] == "nutree_monthly"
    assert "raw" not in json.dumps(body["events"])
