"""Composition of the entitlement ledger services (NM-680)."""

import pytest
from pydantic import ValidationError

from src.bootstrap import entitlements as bootstrap
from src.infra.config.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_singletons():
    bootstrap.reset_entitlement_singletons_for_tests()
    yield
    bootstrap.reset_entitlement_singletons_for_tests()


def test_budget_and_flag_reader_are_process_singletons():
    assert bootstrap.get_rc_budget() is bootstrap.get_rc_budget()
    assert bootstrap.get_feature_flag_reader() is bootstrap.get_feature_flag_reader()


def test_services_share_the_process_budget():
    sync = bootstrap.get_entitlement_sync_service()
    query = bootstrap.get_entitlement_query_service()

    assert sync._budget is bootstrap.get_rc_budget()
    assert query._flags is bootstrap.get_feature_flag_reader()
    assert sync.provider_name == "revenuecat"


def test_access_config_reads_sandbox_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ENTITLEMENT_SANDBOX_POLICY", "allowlist")
    monkeypatch.setattr(settings, "ENTITLEMENT_SANDBOX_ALLOWED_USER_IDS", " a, b ,")

    config = bootstrap.get_entitlement_access_config()

    assert config.sandbox_allowed("a") is True
    assert config.sandbox_allowed("c") is False


def test_settings_defaults_preserve_todays_behaviour():
    fields = Settings.model_fields
    assert fields["ENTITLEMENT_SANDBOX_POLICY"].default == "all"
    assert fields["ENTITLEMENT_SANDBOX_ALLOWED_USER_IDS"].default == ""
    assert fields["ENTITLEMENT_RC_RPS"].default == 2.0
    assert fields["ENTITLEMENT_RC_CONCURRENCY"].default == 4


@pytest.mark.parametrize(
    ("raw", "expected"), [(" AllowList ", "allowlist"), ("NONE", "none"), ("", "all")]
)
def test_sandbox_policy_is_normalised(raw, expected):
    assert Settings(ENTITLEMENT_SANDBOX_POLICY=raw).ENTITLEMENT_SANDBOX_POLICY == (
        expected
    )


@pytest.mark.parametrize("raw", ["allow_list", "allow-list", "off"])
def test_unknown_sandbox_policy_fails_at_startup(raw):
    """A typo meant to restrict sandbox access must not fail open to ``all``."""
    with pytest.raises(ValidationError):
        Settings(ENTITLEMENT_SANDBOX_POLICY=raw)


def test_grant_handlers_are_composed():
    assert bootstrap.get_create_entitlement_grant_handler()._sync is not None
    assert bootstrap.get_revoke_entitlement_grant_handler()._sync is not None


@pytest.mark.asyncio
async def test_flag_reader_without_database_defaults_off(monkeypatch):
    monkeypatch.setattr(bootstrap.config_async, "AsyncSessionLocal", None)

    reader = bootstrap.get_feature_flag_reader()

    assert await reader.is_enabled("ent_read_api") is False
