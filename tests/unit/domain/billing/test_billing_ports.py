"""Contracts of the NM-680 domain ports."""

import inspect
from datetime import UTC, datetime

import pytest

from src.domain.exceptions.billing_exceptions import ProviderNotFound
from src.domain.model.billing import CustomerSnapshot
from src.domain.ports import billing_provider_port
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.domain.ports.entitlement_ledger_repository_port import (
    BillingCustomerIdentityRepositoryPort,
    BillingProviderEventRepositoryPort,
    EntitlementGrantRepositoryPort,
    IdentityLinkOutcome,
    StoreSubscriptionRepositoryPort,
    UserEntitlementRepositoryPort,
)
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.domain.services.billing.revenuecat_normalizer import (
    normalize_revenuecat_subscriber,
)

PORTS = [
    BillingProviderPort,
    FeatureFlagReaderPort,
    BillingCustomerIdentityRepositoryPort,
    BillingProviderEventRepositoryPort,
    StoreSubscriptionRepositoryPort,
    EntitlementGrantRepositoryPort,
    UserEntitlementRepositoryPort,
]


@pytest.mark.parametrize("port", PORTS, ids=lambda port: port.__name__)
def test_every_abstract_method_is_async(port):
    abstract = sorted(port.__abstractmethods__)

    assert abstract
    assert [
        name
        for name in abstract
        if not inspect.iscoroutinefunction(getattr(port, name))
    ] == []


@pytest.mark.parametrize("port", PORTS, ids=lambda port: port.__name__)
def test_ports_cannot_be_instantiated(port):
    with pytest.raises(TypeError):
        port()


def test_ledger_repository_method_sets():
    assert {
        "get_primary",
        "list_for_user",
        "find_by_customer_id",
        "upsert_primary",
        "add_alias",
        "retire_for_user",
    } == BillingCustomerIdentityRepositoryPort.__abstractmethods__
    assert {
        "insert_if_absent",
        "get",
        "get_by_provider_event_id",
        "claim_due",
        "mark_processed",
        "mark_retry",
        "mark_dead",
        "mark_ignored",
        "park",
        "purge_raw_for_user",
        "purge_raw_received_before",
        "set_user",
        "requeue",
        "list_unmatched",
        "list_for_user",
    } == BillingProviderEventRepositoryPort.__abstractmethods__
    assert {
        "list_for_user",
        "list_for_customer",
        "get_by_keys",
        "lock_keys",
        "upsert",
        "upsert_many",
    } == StoreSubscriptionRepositoryPort.__abstractmethods__
    assert {
        "add",
        "get",
        "find_by_source_ref",
        "list_for_user",
        "revoke",
    } == EntitlementGrantRepositoryPort.__abstractmethods__
    assert {
        "lock_users",
        "get",
        "list_for_user",
        "upsert",
        "set_last_purchase_hint_at",
        "list_due_for_recompute",
        "list_user_ids_for_resync",
    } == UserEntitlementRepositoryPort.__abstractmethods__


def test_identity_link_outcomes():
    assert {outcome.value for outcome in IdentityLinkOutcome} == {
        "inserted",
        "already_linked",
        "conflict",
        "reclaimed",
        "retired",
    }


def test_provider_port_re_exports_typed_errors():
    for name in (
        "BillingProviderError",
        "ProviderUnavailable",
        "ProviderRateLimited",
        "ProviderNotFound",
        "InvalidProviderPayload",
    ):
        assert name in billing_provider_port.__all__
        assert hasattr(billing_provider_port, name)


class _InMemoryProvider(BillingProviderPort):
    provider = "revenuecat"

    def __init__(self, bodies):
        self._bodies = bodies

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        if customer_id not in self._bodies:
            raise ProviderNotFound(customer_id, provider=self.provider)
        return normalize_revenuecat_subscriber(
            self._bodies[customer_id],
            fetched_at=datetime(2026, 10, 15, 8, 0, tzinfo=UTC),
            requested_customer_id=customer_id,
        )


@pytest.mark.asyncio
async def test_provider_port_contract_with_normalizer(rc_body):
    provider = _InMemoryProvider({"Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1": rc_body("trial")})

    snapshot = await provider.fetch_customer("Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1")

    assert snapshot.canonical_customer_id == "Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1"
    with pytest.raises(ProviderNotFound):
        await provider.fetch_customer("unknown")


class _StaticFlags(FeatureFlagReaderPort):
    def __init__(self, flags):
        self._flags = flags

    async def is_enabled(self, name: str, default: bool = False) -> bool:
        return self._flags.get(name, default)


@pytest.mark.asyncio
async def test_feature_flag_reader_defaults_off():
    flags = _StaticFlags({"ent_read_api": True})

    assert await flags.is_enabled("ent_read_api") is True
    assert await flags.is_enabled("ent_ledger_write") is False
    assert await flags.is_enabled("ent_ledger_write", default=True) is True
