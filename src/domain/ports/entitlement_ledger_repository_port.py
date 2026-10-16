"""Repository ports for the entitlement ledger (NM-680).

Registered on the async unit of work as ``billing_identities``,
``billing_events``, ``store_subscriptions``, ``entitlement_grants`` and
``user_entitlements``. Every method runs inside the caller's transaction and
none of them commits.

Only the entitlement sync service writes ``store_subscriptions`` and
``user_entitlements``; only the admin grant handlers write
``entitlement_grants``.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from src.domain.model.billing.constants import (
    DEFAULT_PROJECT,
    ENTITLEMENT_STANDARD,
    PROVIDER_REVENUECAT,
)
from src.domain.model.billing.ledger import (
    BillingCustomerIdentity,
    BillingProviderEvent,
    EntitlementGrant,
    StoreSubscription,
    UserEntitlement,
)


class IdentityLinkOutcome(StrEnum):
    INSERTED = "inserted"
    # The id already belongs to this user; ``last_seen_at`` was refreshed.
    ALREADY_LINKED = "already_linked"
    # The id belongs to another user. Nothing was written; the caller logs it.
    CONFLICT = "conflict"
    # ``upsert_primary`` took the id from another user's non-primary row.
    RECLAIMED = "reclaimed"
    # The id is this user's own retired row (deleted account, D11). Nothing was
    # written: only a different, re-registered user may reclaim it.
    RETIRED = "retired"


class BillingCustomerIdentityRepositoryPort(ABC):
    """``billing_customer_identities``: one user, many provider customer ids."""

    @abstractmethod
    async def get_primary(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        project: str = DEFAULT_PROJECT,
    ) -> BillingCustomerIdentity | None:
        """The user's live (not retired) primary identity."""

    @abstractmethod
    async def list_for_user(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        *,
        include_retired: bool = False,
    ) -> list[BillingCustomerIdentity]:
        """Primary first, then aliases, anonymous ids and legacy lead ids."""

    @abstractmethod
    async def find_by_customer_id(
        self,
        provider: str,
        provider_customer_id: str,
        project: str = DEFAULT_PROJECT,
        *,
        include_retired: bool = False,
    ) -> BillingCustomerIdentity | None:
        """Resolve a provider id (webhook ``app_user_id``, alias) to its owner."""

    @abstractmethod
    async def upsert_primary(
        self,
        user_id: str,
        provider: str,
        provider_customer_id: str,
        *,
        seen_at: datetime,
        project: str = DEFAULT_PROJECT,
    ) -> IdentityLinkOutcome:
        """Seed or refresh the user's primary identity (``/v1/users/sync``).

        ``provider_customer_id`` is always the user's own ``users.firebase_uid``.
        A live non-primary row (alias, anonymous, legacy lead) holding it for
        another user is wrong by construction and is taken over (``RECLAIMED``);
        another user's live primary row is a ``CONFLICT``.
        """

    @abstractmethod
    async def add_alias(
        self,
        user_id: str,
        provider: str,
        provider_customer_id: str,
        kind: str,
        *,
        seen_at: datetime,
        project: str = DEFAULT_PROJECT,
    ) -> IdentityLinkOutcome:
        """``INSERT ... ON CONFLICT DO NOTHING``; a conflict never fails a sync."""

    @abstractmethod
    async def retire_for_user(self, user_id: str, *, retired_at: datetime) -> int:
        """Account deletion (D11): retire every live identity. Returns the count."""


class BillingProviderEventRepositoryPort(ABC):
    """``billing_provider_events``: the webhook inbox, doubling as the sync queue."""

    @abstractmethod
    async def insert_if_absent(self, event: BillingProviderEvent) -> bool:
        """``ON CONFLICT (provider, provider_event_id) DO NOTHING``. True if inserted."""

    @abstractmethod
    async def get(self, event_id: str) -> BillingProviderEvent | None:
        pass

    @abstractmethod
    async def get_by_provider_event_id(
        self, provider: str, provider_event_id: str
    ) -> BillingProviderEvent | None:
        pass

    @abstractmethod
    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> list[BillingProviderEvent]:
        """Lease due rows with ``FOR UPDATE SKIP LOCKED`` and mark them ``processing``.

        Due = ``received``/``retry`` with ``next_attempt_at`` null or ``<= now``,
        plus ``processing`` rows whose lease has expired (a crashed worker).
        """

    @abstractmethod
    async def mark_processed(
        self,
        event_id: str,
        *,
        processed_at: datetime,
        user_id: str | None = None,
    ) -> None:
        """Settle the row and clear its lease."""

    @abstractmethod
    async def mark_retry(
        self, event_id: str, *, error: str, next_attempt_at: datetime
    ) -> None:
        """Increment ``attempts``, record the error, clear the lease, set ``retry``."""

    @abstractmethod
    async def mark_dead(self, event_id: str, *, error: str) -> None:
        """Increment ``attempts`` and give up (after ``INBOX_MAX_ATTEMPTS``)."""

    @abstractmethod
    async def mark_ignored(self, event_id: str, *, reason: str | None = None) -> None:
        """Settle an event that needs no sync (e.g. RevenueCat ``TEST``)."""

    @abstractmethod
    async def park(self, event_id: str, *, reason: str, drop_raw: bool = False) -> None:
        """Set ``unmatched`` with ``park_reason`` (unknown or deleted user).

        ``drop_raw`` also nulls the stored body (a deleted account, D11).
        """

    @abstractmethod
    async def purge_raw_for_user(
        self, user_id: str, *, app_user_ids: Sequence[str]
    ) -> int:
        """Account deletion (D9, D11): null ``raw`` on the user's rows and on
        parked rows addressed to one of ``app_user_ids``. Returns the count."""

    @abstractmethod
    async def purge_raw_received_before(self, cutoff: datetime, *, limit: int) -> int:
        """Retention (D9): null ``raw`` on up to ``limit`` of the oldest rows
        received before ``cutoff``. Returns the count."""

    @abstractmethod
    async def set_user(self, event_id: str, user_id: str) -> None:
        """Record the user the webhook route matched the delivery to."""

    @abstractmethod
    async def requeue(
        self, event_ids: Sequence[str], *, user_id: str, now: datetime
    ) -> int:
        """Replay parked rows for a now-resolved user: ``received``, due ``now``."""

    @abstractmethod
    async def list_unmatched(
        self,
        *,
        limit: int,
        app_user_ids: Sequence[str] | None = None,
        after: tuple[datetime, str] | None = None,
    ) -> list[BillingProviderEvent]:
        """Oldest parked rows first, optionally only for these provider ids.

        ``after`` is a ``(received_at, id)`` keyset cursor, so a sweep can page
        past rows that still do not resolve instead of re-reading them forever.
        """

    @abstractmethod
    async def list_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> list[BillingProviderEvent]:
        """Newest first (admin debug view)."""


class StoreSubscriptionRepositoryPort(ABC):
    """``store_subscriptions``: one row per subscription key, snapshot-replaced."""

    @abstractmethod
    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_absent: bool = True,
    ) -> list[StoreSubscription]:
        pass

    @abstractmethod
    async def list_for_customer(
        self,
        provider: str,
        source_customer_id: str,
        *,
        user_id: str | None = None,
    ) -> list[StoreSubscription]:
        """Rows sourced from one provider customer (snapshot replace scope)."""

    @abstractmethod
    async def get_by_keys(
        self, provider: str, keys: Sequence[tuple[str, str]]
    ) -> list[StoreSubscription]:
        """Rows matching ``(environment, subscription_key)`` pairs, for any user."""

    @abstractmethod
    async def lock_keys(self, provider: str, keys: Sequence[tuple[str, str]]) -> None:
        """Transaction-scoped advisory lock per ``(environment, subscription_key)``.

        Taken before any user lock, so a key changes owner only while its lock is
        held and the previous owner is always known (and locked) in time.
        """

    @abstractmethod
    async def upsert(self, row: StoreSubscription) -> StoreSubscription:
        """Insert, or update every column but ``id``/``created_at`` on the
        ``(provider, environment, subscription_key)`` conflict."""

    @abstractmethod
    async def upsert_many(self, rows: Sequence[StoreSubscription]) -> None:
        pass


class EntitlementGrantRepositoryPort(ABC):
    """``entitlement_grants``: append-only, revoke-only manual grants (D5)."""

    @abstractmethod
    async def add(self, grant: EntitlementGrant) -> EntitlementGrant:
        pass

    @abstractmethod
    async def get(self, grant_id: str) -> EntitlementGrant | None:
        pass

    @abstractmethod
    async def find_by_source_ref(
        self,
        source: str,
        source_ref: str,
        entitlement_key: str = ENTITLEMENT_STANDARD,
    ) -> EntitlementGrant | None:
        """Idempotency lookup backed by ``uq_eg_source``."""

    @abstractmethod
    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_revoked: bool = True,
    ) -> list[EntitlementGrant]:
        pass

    @abstractmethod
    async def revoke(
        self, grant_id: str, *, revoked_at: datetime, reason: str, revoked_by: str
    ) -> EntitlementGrant | None:
        """Revoke once, recording the admin who did (``revoked_by``). Returns
        None when missing; an already revoked grant is returned unchanged."""


class UserEntitlementRepositoryPort(ABC):
    """``user_entitlements``: the projection, primary key (user_id, entitlement_key)."""

    @abstractmethod
    async def lock_users(self, user_ids: Sequence[str]) -> None:
        """Transaction-scoped ``pg_advisory_xact_lock(hashtext('ent:'||user_id))``
        for each distinct id, taken in sorted order to avoid deadlocks."""

    @abstractmethod
    async def get(
        self, user_id: str, entitlement_key: str = ENTITLEMENT_STANDARD
    ) -> UserEntitlement | None:
        pass

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[UserEntitlement]:
        pass

    @abstractmethod
    async def upsert(self, projection: UserEntitlement) -> None:
        pass

    @abstractmethod
    async def set_last_purchase_hint_at(
        self, user_id: str, entitlement_key: str, *, at: datetime
    ) -> None:
        """No-op when the projection row does not exist yet."""

    @abstractmethod
    async def list_due_for_recompute(
        self, *, now: datetime, limit: int
    ) -> list[tuple[str, str]]:
        """``(user_id, entitlement_key)`` with ``next_change_at <= now``, earliest first."""

    @abstractmethod
    async def list_user_ids_for_resync(
        self, *, synced_before: datetime, limit: int
    ) -> list[str]:
        """Active users with ``has_access`` whose ``last_provider_sync_at`` is
        null or older than ``synced_before``, stalest first.

        Deleted (inactive) accounts are never listed: their sync is refused,
        so they would otherwise stay stalest and starve every later batch.
        """
