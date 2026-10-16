"""Composition of the entitlement API, inbox and worker services (NM-680)."""

import pytest

from src.api import base_dependencies
from src.app.services.entitlement_account_service import EntitlementAccountService
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.bootstrap import entitlements as bootstrap
from src.domain.model.billing.constants import SyncLane


@pytest.fixture(autouse=True)
def _reset_singletons():
    bootstrap.reset_entitlement_singletons_for_tests()
    yield
    bootstrap.reset_entitlement_singletons_for_tests()


def test_account_service_shares_process_singletons():
    first = bootstrap.get_entitlement_account_service()
    second = bootstrap.get_entitlement_account_service()

    assert isinstance(first, EntitlementAccountService)
    assert first._scheduler is second._scheduler is bootstrap.get_resync_scheduler()
    assert first._limiter is second._limiter is bootstrap.get_sync_rate_limiter()
    assert first._flags is bootstrap.get_feature_flag_reader()
    assert first._sync._budget is bootstrap.get_rc_budget()


def test_base_dependency_getters_delegate_to_bootstrap():
    assert isinstance(
        base_dependencies.get_entitlement_inbox_service(), EntitlementInboxService
    )
    assert isinstance(
        base_dependencies.get_entitlement_account_service(), EntitlementAccountService
    )
    assert base_dependencies.get_create_entitlement_grant_handler()._sync is not None
    assert base_dependencies.get_revoke_entitlement_grant_handler()._sync is not None


@pytest.mark.asyncio
async def test_follow_up_sync_runs_below_the_webhook_lane(monkeypatch):
    calls = []

    class _Sync:
        async def sync_user(self, user_id, lane, reason, *, budget_timeout=None):
            calls.append((user_id, lane, reason))

    monkeypatch.setattr(bootstrap, "get_entitlement_sync_service", lambda: _Sync())

    await bootstrap._follow_up_sync("user-1")

    assert calls == [("user-1", SyncLane.LOGIN_SYNC, "purchase_followup")]


def test_hint_rate_limiter_is_process_wide():
    bootstrap.reset_entitlement_singletons_for_tests()
    try:
        assert bootstrap.get_hint_rate_limiter() is bootstrap.get_hint_rate_limiter()
        service = bootstrap.get_entitlement_account_service()
        assert service._hint_limiter is bootstrap.get_hint_rate_limiter()
    finally:
        bootstrap.reset_entitlement_singletons_for_tests()
