"""Entitlement hooks on /v1/users and web-funnel finalize (NM-680)."""

import asyncio
import inspect
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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

from src.api.base_dependencies import get_entitlement_account_service
from src.api.dependencies.auth import (
    verify_firebase_token,
    verify_firebase_uid_ownership,
)
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1 import web_funnel
from src.api.routes.v1.users import router as users_router
from src.app.services.entitlement_account_service import (
    HINTED_SYNC_LIMIT,
    DelayedResyncScheduler,
    EntitlementAccountService,
    SlidingWindowRateLimiter,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.model.billing.constants import (
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_READ_API,
    FLAG_ENT_READ_API_DEFER,
)

U1, UID1 = "user-1", "uid-1"


def _user_payload(has_subscription: bool) -> dict:
    now = datetime.now(UTC)
    return {
        "id": U1,
        "firebase_uid": UID1,
        "email": "a@b.com",
        "username": "u",
        "first_name": None,
        "last_name": None,
        "phone_number": None,
        "display_name": None,
        "photo_url": None,
        "provider": "google",
        "is_active": True,
        "onboarding_completed": True,
        "last_accessed": None,
        "created_at": now,
        "updated_at": now,
        "has_subscription": has_subscription,
        "subscription": None,
    }


class _Bus:
    def __init__(self, legacy_has_subscription: bool):
        self.legacy = legacy_has_subscription

    async def send(self, message):
        if type(message).__name__ == "SyncUserCommand":
            return {
                "user": _user_payload(self.legacy),
                "created": False,
                "updated": False,
                "message": "User data up to date",
            }
        return _user_payload(self.legacy)


class _Env:
    def __init__(self):
        self.uow = FakeUnitOfWork()
        self.uow.users.users[U1] = make_user(U1, UID1)
        self.clock = FakeClock(NOW)
        self.provider = FakeBillingProvider()
        self.provider.responses[UID1] = snapshot(UID1)  # RC: no subscription
        self.flags = StaticFeatureFlagReader()
        factory = lambda: self.uow  # noqa: E731
        self.sync = EntitlementSyncService(
            uow_factory=factory,
            provider=self.provider,
            budget=FakeBudget(),
            clock=self.clock,
        )
        self.service = EntitlementAccountService(
            query_service=EntitlementQueryService(
                uow_factory=factory,
                sync_service=self.sync,
                flags=self.flags,
                clock=self.clock,
            ),
            sync_service=self.sync,
            inbox_service=EntitlementInboxService(
                uow_factory=factory, sync_service=self.sync, clock=self.clock
            ),
            flags=self.flags,
            scheduler=DelayedResyncScheduler(lambda _u: asyncio.sleep(0), delays=()),
            rate_limiter=SlidingWindowRateLimiter(),
            hint_rate_limiter=SlidingWindowRateLimiter(limit=HINTED_SYNC_LIMIT),
            clock=self.clock,
        )


@pytest.fixture
def env():
    return _Env()


def _client(env, *, legacy_has_subscription: bool) -> TestClient:
    app = FastAPI()
    app.include_router(users_router)
    app.dependency_overrides[get_configured_event_bus] = lambda: _Bus(
        legacy_has_subscription
    )
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": UID1}
    app.dependency_overrides[verify_firebase_uid_ownership] = lambda: UID1
    app.dependency_overrides[get_entitlement_account_service] = lambda: env.service
    return TestClient(app)


def _sync(client):
    return client.post(
        "/v1/users/sync",
        json={"firebase_uid": UID1, "email": "a@b.com", "provider": "google"},
    )


def test_flags_off_users_endpoints_are_unchanged(env):
    client = _client(env, legacy_has_subscription=True)

    assert _sync(client).json()["user"]["has_subscription"] is True
    assert client.get(f"/v1/users/firebase/{UID1}").json()["has_subscription"] is True
    assert env.uow.billing_identities.rows == {}
    assert env.provider.calls == []


def test_users_sync_seeds_primary_identity_while_ledger_is_written(env):
    env.flags.set(FLAG_ENT_LEDGER_WRITE)

    assert _sync(_client(env, legacy_has_subscription=False)).status_code == 200

    identity = asyncio.run(env.uow.billing_identities.get_primary(U1))
    assert identity is not None and identity.provider_customer_id == UID1


def test_has_subscription_comes_from_the_projection_with_read_api_on(env):
    env.flags.set(FLAG_ENT_READ_API)
    client = _client(env, legacy_has_subscription=True)
    # Never synced: the ledger cannot say "no", the legacy value stands.
    assert client.get(f"/v1/users/firebase/{UID1}").json()["has_subscription"] is True

    asyncio.run(env.sync.sync_user(U1, "login_sync", "test"))

    # Synced and RC reports nothing: the development override is not applied.
    assert client.get(f"/v1/users/firebase/{UID1}").json()["has_subscription"] is False
    assert _sync(client).json()["user"]["has_subscription"] is False


def test_projection_yes_overrides_a_legacy_no(env):
    env.flags.set(FLAG_ENT_READ_API)
    env.provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    asyncio.run(env.sync.sync_user(U1, "login_sync", "test"))

    client = _client(env, legacy_has_subscription=False)

    assert _sync(client).json()["user"]["has_subscription"] is True


def test_defer_kill_switch_restores_the_legacy_value(env):
    env.flags.set(FLAG_ENT_READ_API)
    env.flags.set(FLAG_ENT_READ_API_DEFER)
    asyncio.run(env.sync.sync_user(U1, "login_sync", "test"))

    client = _client(env, legacy_has_subscription=True)

    assert client.get(f"/v1/users/firebase/{UID1}").json()["has_subscription"] is True


# ---- web-funnel finalize ----


def test_finalize_runs_no_entitlement_sync():
    """Finalize holds row locks and the app's hinted /sync fetches after it, so
    it must not run a second purchase-lane RevenueCat fetch itself."""
    source = inspect.getsource(web_funnel)

    assert "get_entitlement_account_service" not in source
    assert "entitlements" not in inspect.getsource(
        web_funnel.finalize_revenuecat_redemption
    )
