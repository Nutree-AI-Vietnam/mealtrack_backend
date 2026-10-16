"""In-memory fakes for the entitlement ledger repository ports (NM-680).

They mirror the database constraints the services rely on: unique customer ids,
one live primary identity per user, one store row per subscription key and one
grant per ``(source, source_ref, entitlement_key)``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from src.domain.model.billing.constants import (
    DEFAULT_PROJECT,
    ENTITLEMENT_STANDARD,
    PROVIDER_REVENUECAT,
    IdentityKind,
    ProviderEventStatus,
    StoreSubscriptionState,
)
from src.domain.model.billing.ledger import (
    BillingCustomerIdentity,
    BillingProviderEvent,
    EntitlementGrant,
    StoreSubscription,
    UserEntitlement,
)
from src.domain.ports.entitlement_ledger_repository_port import (
    BillingCustomerIdentityRepositoryPort,
    BillingProviderEventRepositoryPort,
    EntitlementGrantRepositoryPort,
    IdentityLinkOutcome,
    StoreSubscriptionRepositoryPort,
    UserEntitlementRepositoryPort,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FakeBillingCustomerIdentityRepository(BillingCustomerIdentityRepositoryPort):
    def __init__(self) -> None:
        self.rows: dict[str, BillingCustomerIdentity] = {}
        # Set to make add_alias behave like a failed (but swallowed) insert.
        self.fail_alias_inserts = False

    async def get_primary(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        project: str = DEFAULT_PROJECT,
    ) -> BillingCustomerIdentity | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.user_id == user_id
                and row.provider == provider
                and row.project == project
                and row.kind == IdentityKind.PRIMARY
                and row.retired_at is None
            ),
            None,
        )

    async def list_for_user(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        *,
        include_retired: bool = False,
    ) -> list[BillingCustomerIdentity]:
        rows = [
            row
            for row in self.rows.values()
            if row.user_id == user_id
            and row.provider == provider
            and (include_retired or row.retired_at is None)
        ]
        return sorted(
            rows,
            key=lambda row: (row.kind != IdentityKind.PRIMARY, row.first_seen_at),
        )

    async def find_by_customer_id(
        self,
        provider: str,
        provider_customer_id: str,
        project: str = DEFAULT_PROJECT,
        *,
        include_retired: bool = False,
    ) -> BillingCustomerIdentity | None:
        row = self._by_customer(provider, project, provider_customer_id)
        if row is None or (row.retired_at is not None and not include_retired):
            return None
        return row

    async def upsert_primary(
        self,
        user_id: str,
        provider: str,
        provider_customer_id: str,
        *,
        seen_at: datetime,
        project: str = DEFAULT_PROJECT,
    ) -> IdentityLinkOutcome:
        existing = self._by_customer(provider, project, provider_customer_id)
        if existing is not None and existing.retired_at is None:
            if existing.user_id != user_id:
                if existing.kind == IdentityKind.PRIMARY:
                    return IdentityLinkOutcome.CONFLICT
                self._demote_primary(user_id, provider, project)
                self.rows[existing.id] = replace(
                    existing,
                    user_id=user_id,
                    kind=IdentityKind.PRIMARY,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    updated_at=seen_at,
                )
                return IdentityLinkOutcome.RECLAIMED
            if existing.kind != IdentityKind.PRIMARY:
                self._demote_primary(user_id, provider, project)
            self.rows[existing.id] = replace(
                existing,
                kind=IdentityKind.PRIMARY,
                last_seen_at=max(existing.last_seen_at, seen_at),
            )
            return IdentityLinkOutcome.ALREADY_LINKED
        if existing is not None and existing.user_id == user_id:
            return IdentityLinkOutcome.RETIRED
        self._demote_primary(user_id, provider, project)
        if existing is not None:
            self.rows[existing.id] = replace(
                existing,
                user_id=user_id,
                kind=IdentityKind.PRIMARY,
                retired_at=None,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            return IdentityLinkOutcome.INSERTED
        self._insert(
            user_id, provider, project, provider_customer_id, "primary", seen_at
        )
        return IdentityLinkOutcome.INSERTED

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
        if kind == IdentityKind.PRIMARY or kind not in set(IdentityKind):
            raise ValueError(f"invalid alias kind: {kind}")
        if self.fail_alias_inserts:
            return IdentityLinkOutcome.CONFLICT
        existing = self._by_customer(provider, project, provider_customer_id)
        if existing is None:
            self._insert(
                user_id, provider, project, provider_customer_id, kind, seen_at
            )
            return IdentityLinkOutcome.INSERTED
        if existing.user_id != user_id:
            return IdentityLinkOutcome.CONFLICT
        if existing.last_seen_at < seen_at:
            self.rows[existing.id] = replace(existing, last_seen_at=seen_at)
        return IdentityLinkOutcome.ALREADY_LINKED

    async def retire_for_user(self, user_id: str, *, retired_at: datetime) -> int:
        count = 0
        for row in list(self.rows.values()):
            if row.user_id == user_id and row.retired_at is None:
                self.rows[row.id] = replace(row, retired_at=retired_at)
                count += 1
        return count

    def _by_customer(
        self, provider: str, project: str, provider_customer_id: str
    ) -> BillingCustomerIdentity | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.provider == provider
                and row.project == project
                and row.provider_customer_id == provider_customer_id
            ),
            None,
        )

    def _demote_primary(self, user_id: str, provider: str, project: str) -> None:
        for row in list(self.rows.values()):
            if (
                row.user_id == user_id
                and row.provider == provider
                and row.project == project
                and row.kind == IdentityKind.PRIMARY
                and row.retired_at is None
            ):
                self.rows[row.id] = replace(row, kind=IdentityKind.ALIAS)

    def _insert(
        self,
        user_id: str,
        provider: str,
        project: str,
        provider_customer_id: str,
        kind: str,
        seen_at: datetime,
    ) -> None:
        row_id = str(uuid.uuid4())
        self.rows[row_id] = BillingCustomerIdentity(
            id=row_id,
            user_id=user_id,
            provider=provider,
            project=project,
            provider_customer_id=provider_customer_id,
            kind=kind,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            created_at=seen_at,
            updated_at=seen_at,
        )


class FakeBillingProviderEventRepository(BillingProviderEventRepositoryPort):
    def __init__(self) -> None:
        self.rows: dict[str, BillingProviderEvent] = {}

    async def insert_if_absent(self, event: BillingProviderEvent) -> bool:
        if await self.get_by_provider_event_id(event.provider, event.provider_event_id):
            return False
        self.rows[event.id] = event
        return True

    async def get(self, event_id: str) -> BillingProviderEvent | None:
        return self.rows.get(event_id)

    async def get_by_provider_event_id(
        self, provider: str, provider_event_id: str
    ) -> BillingProviderEvent | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.provider == provider
                and row.provider_event_id == provider_event_id
            ),
            None,
        )

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> list[BillingProviderEvent]:
        def due(row: BillingProviderEvent) -> bool:
            if row.status in (ProviderEventStatus.RECEIVED, ProviderEventStatus.RETRY):
                return row.next_attempt_at is None or row.next_attempt_at <= now
            return (
                row.status == ProviderEventStatus.PROCESSING
                and row.lease_expires_at is not None
                and row.lease_expires_at <= now
            )

        candidates = sorted(
            (row for row in self.rows.values() if due(row)),
            key=lambda row: (row.next_attempt_at or row.received_at, row.received_at),
        )[: max(limit, 0)]
        claimed = []
        for row in candidates:
            updated = replace(
                row,
                status=ProviderEventStatus.PROCESSING,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )
            self.rows[row.id] = updated
            claimed.append(updated)
        return claimed

    async def mark_processed(
        self,
        event_id: str,
        *,
        processed_at: datetime,
        user_id: str | None = None,
    ) -> None:
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=ProviderEventStatus.PROCESSED,
            processed_at=processed_at,
            user_id=user_id or row.user_id,
            next_attempt_at=None,
            park_reason=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    async def mark_retry(
        self, event_id: str, *, error: str, next_attempt_at: datetime
    ) -> None:
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=ProviderEventStatus.RETRY,
            attempts=row.attempts + 1,
            last_error=error,
            next_attempt_at=next_attempt_at,
            lease_owner=None,
            lease_expires_at=None,
        )

    async def mark_dead(self, event_id: str, *, error: str) -> None:
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=ProviderEventStatus.DEAD,
            attempts=row.attempts + 1,
            last_error=error,
            next_attempt_at=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    async def mark_ignored(self, event_id: str, *, reason: str | None = None) -> None:
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=ProviderEventStatus.IGNORED,
            park_reason=reason,
            processed_at=datetime.now(UTC),
            lease_owner=None,
            lease_expires_at=None,
        )

    async def park(self, event_id: str, *, reason: str, drop_raw: bool = False) -> None:
        row = self.rows[event_id]
        self.rows[event_id] = replace(
            row,
            status=ProviderEventStatus.UNMATCHED,
            park_reason=reason,
            next_attempt_at=None,
            lease_owner=None,
            lease_expires_at=None,
            raw=None if drop_raw else row.raw,
        )

    async def purge_raw_for_user(
        self, user_id: str, *, app_user_ids: Sequence[str]
    ) -> int:
        count = 0
        for row in list(self.rows.values()):
            owned = row.user_id == user_id or (
                row.status == ProviderEventStatus.UNMATCHED
                and row.app_user_id in app_user_ids
            )
            if owned and row.raw is not None:
                self.rows[row.id] = replace(row, raw=None)
                count += 1
        return count

    async def purge_raw_received_before(self, cutoff: datetime, *, limit: int) -> int:
        due = sorted(
            (
                row
                for row in self.rows.values()
                if row.raw is not None and row.received_at < cutoff
            ),
            key=lambda row: row.received_at,
        )[:limit]
        for row in due:
            self.rows[row.id] = replace(row, raw=None)
        return len(due)

    async def set_user(self, event_id: str, user_id: str) -> None:
        self.rows[event_id] = replace(self.rows[event_id], user_id=user_id)

    async def requeue(
        self, event_ids: Sequence[str], *, user_id: str, now: datetime
    ) -> int:
        count = 0
        for event_id in event_ids:
            row = self.rows.get(event_id)
            if row is None or row.status != ProviderEventStatus.UNMATCHED:
                continue
            self.rows[event_id] = replace(
                row,
                status=ProviderEventStatus.RECEIVED,
                user_id=user_id,
                park_reason=None,
                next_attempt_at=now,
            )
            count += 1
        return count

    async def list_unmatched(
        self,
        *,
        limit: int,
        app_user_ids: Sequence[str] | None = None,
        after: tuple[datetime, str] | None = None,
    ) -> list[BillingProviderEvent]:
        rows = [
            row
            for row in self.rows.values()
            if row.status == ProviderEventStatus.UNMATCHED
            and (app_user_ids is None or row.app_user_id in app_user_ids)
            and (after is None or (row.received_at, row.id) > after)
        ]
        return sorted(rows, key=lambda row: (row.received_at, row.id))[:limit]

    async def list_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> list[BillingProviderEvent]:
        rows = [row for row in self.rows.values() if row.user_id == user_id]
        return sorted(rows, key=lambda row: row.received_at, reverse=True)[:limit]


class FakeStoreSubscriptionRepository(StoreSubscriptionRepositoryPort):
    def __init__(self) -> None:
        # Keyed like uq_ss_key: (provider, environment, subscription_key).
        self.rows: dict[tuple[str, str, str], StoreSubscription] = {}
        self.lock_calls: list[list[str]] = []

    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_absent: bool = True,
    ) -> list[StoreSubscription]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id
            and (entitlement_key is None or row.entitlement_key == entitlement_key)
            and (include_absent or row.state != StoreSubscriptionState.ABSENT)
        ]

    async def list_for_customer(
        self,
        provider: str,
        source_customer_id: str,
        *,
        user_id: str | None = None,
    ) -> list[StoreSubscription]:
        return [
            row
            for row in self.rows.values()
            if row.provider == provider
            and row.source_customer_id == source_customer_id
            and (user_id is None or row.user_id == user_id)
        ]

    async def get_by_keys(
        self, provider: str, keys: Sequence[tuple[str, str]]
    ) -> list[StoreSubscription]:
        return [
            self.rows[(provider, environment, key)]
            for environment, key in keys
            if (provider, environment, key) in self.rows
        ]

    async def lock_keys(self, provider: str, keys: Sequence[tuple[str, str]]) -> None:
        self.lock_calls.append(sorted(f"{provider}:{e}:{k}" for e, k in keys))

    async def upsert(self, row: StoreSubscription) -> StoreSubscription:
        key = (row.provider, row.environment, row.subscription_key)
        existing = self.rows.get(key)
        if existing is not None:
            row = replace(row, id=existing.id, created_at=existing.created_at)
        self.rows[key] = row
        return row

    async def upsert_many(self, rows: Sequence[StoreSubscription]) -> None:
        for row in rows:
            await self.upsert(row)


class FakeEntitlementGrantRepository(EntitlementGrantRepositoryPort):
    def __init__(self) -> None:
        self.rows: dict[str, EntitlementGrant] = {}

    async def add(self, grant: EntitlementGrant) -> EntitlementGrant:
        if grant.source_ref is not None and await self.find_by_source_ref(
            grant.source, grant.source_ref, grant.entitlement_key
        ):
            raise ValueError("uq_eg_source violated")
        stored = replace(grant, created_at=grant.created_at or datetime.now(UTC))
        self.rows[stored.id] = stored
        return stored

    async def get(self, grant_id: str) -> EntitlementGrant | None:
        return self.rows.get(grant_id)

    async def find_by_source_ref(
        self,
        source: str,
        source_ref: str,
        entitlement_key: str = ENTITLEMENT_STANDARD,
    ) -> EntitlementGrant | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.source == source
                and row.source_ref == source_ref
                and row.entitlement_key == entitlement_key
            ),
            None,
        )

    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_revoked: bool = True,
    ) -> list[EntitlementGrant]:
        rows = [
            row
            for row in self.rows.values()
            if row.user_id == user_id
            and (entitlement_key is None or row.entitlement_key == entitlement_key)
            and (include_revoked or row.revoked_at is None)
        ]
        return sorted(rows, key=lambda row: row.starts_at)

    async def revoke(
        self, grant_id: str, *, revoked_at: datetime, reason: str, revoked_by: str
    ) -> EntitlementGrant | None:
        row = self.rows.get(grant_id)
        if row is None or row.revoked_at is not None:
            return row
        self.rows[grant_id] = row.revoked(at=revoked_at, reason=reason, by=revoked_by)
        return self.rows[grant_id]


class FakeUserEntitlementRepository(UserEntitlementRepositoryPort):
    def __init__(self, users: Any = None) -> None:
        # The fake UoW's user store, standing in for the join on active users.
        self._users = users
        self.rows: dict[tuple[str, str], UserEntitlement] = {}
        # Every lock_users call, in the order the locks were taken.
        self.lock_calls: list[list[str]] = []

    async def lock_users(self, user_ids: Sequence[str]) -> None:
        self.lock_calls.append(sorted(set(user_ids)))

    async def get(
        self, user_id: str, entitlement_key: str = ENTITLEMENT_STANDARD
    ) -> UserEntitlement | None:
        return self.rows.get((user_id, entitlement_key))

    async def list_for_user(self, user_id: str) -> list[UserEntitlement]:
        return sorted(
            (row for row in self.rows.values() if row.user_id == user_id),
            key=lambda row: row.entitlement_key,
        )

    async def upsert(self, projection: UserEntitlement) -> None:
        self.rows[(projection.user_id, projection.entitlement_key)] = projection

    async def set_last_purchase_hint_at(
        self, user_id: str, entitlement_key: str, *, at: datetime
    ) -> None:
        row = self.rows.get((user_id, entitlement_key))
        if row is not None:
            self.rows[(user_id, entitlement_key)] = replace(
                row, last_purchase_hint_at=at
            )

    async def list_due_for_recompute(
        self, *, now: datetime, limit: int
    ) -> list[tuple[str, str]]:
        due = sorted(
            (
                row
                for row in self.rows.values()
                if row.next_change_at is not None and row.next_change_at <= now
            ),
            key=lambda row: row.next_change_at or _EPOCH,
        )
        return [(row.user_id, row.entitlement_key) for row in due[:limit]]

    async def list_user_ids_for_resync(
        self, *, synced_before: datetime, limit: int
    ) -> list[str]:
        stalest: dict[str, datetime] = {}
        for row in self.rows.values():
            synced = row.last_provider_sync_at
            if not row.has_access or (synced is not None and synced >= synced_before):
                continue
            if self._users is not None:
                user = self._users.users.get(row.user_id)
                if user is None or not user.is_active:
                    continue
            moment = synced or _EPOCH
            stalest[row.user_id] = min(stalest.get(row.user_id, moment), moment)
        return sorted(stalest, key=lambda user_id: stalest[user_id])[:limit]
