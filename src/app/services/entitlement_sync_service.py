"""Entitlement sync: provider snapshot -> store_subscriptions -> user_entitlements.

``EntitlementSyncService`` is the only writer of ``store_subscriptions`` and
``user_entitlements`` (NM-680, plan §6). Every sync path (webhook, ``/sync``,
reconciler, backfill) goes through ``sync_user``:

1. Resolve the user's RevenueCat ids: ``users.firebase_uid`` plus any
   ``legacy_lead`` identities.
2. Take a token from the shared provider budget and fetch each customer
   OUTSIDE any DB transaction.
3. In one short transaction, under advisory locks on the snapshot's
   subscription keys and then on every user involved
   (``pg_advisory_xact_lock(hashtext('ent:'||user_id))``, in ``user_id``
   order): discard the snapshot if a newer one was already applied,
   snapshot-replace the customer's rows, link aliases (never failing the sync)
   and recompute the projection of the user and of any previous row owner.
4. Provider errors never reduce access: the caller gets the last projection
   with ``stale=True`` and ``sync_error``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from src.domain.exceptions.billing_exceptions import (
    BillingProviderError,
    ProviderNotFound,
)
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    IdentityKind,
    SandboxPolicy,
    StoreSubscriptionState,
    SyncLane,
)
from src.domain.model.billing.ledger import StoreSubscription, UserEntitlement
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.billing_provider_budget_port import BillingProviderBudgetPort
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome
from src.domain.services.billing.access_policy import (
    compute_projection,
    sandbox_access_allowed,
)
from src.domain.utils.timezone_utils import utc_now
from src.observability import distribution_metric, increment_metric

logger = logging.getLogger(__name__)

USER_NOT_FOUND = "user_not_found"
_ANONYMOUS_ID_PREFIX = "$RCAnonymousID:"
_SUBSCRIPTION_KEY = tuple[str, str]  # (environment, subscription_key)


@dataclass(frozen=True)
class EntitlementAccessConfig:
    """Access policy inputs that come from settings (D3)."""

    sandbox_policy: str = SandboxPolicy.ALL
    sandbox_allowed_user_ids: frozenset[str] = frozenset()

    def sandbox_allowed(self, user_id: str) -> bool:
        return sandbox_access_allowed(
            self.sandbox_policy, user_id, self.sandbox_allowed_user_ids
        )


@dataclass(frozen=True)
class EntitlementSyncResult:
    """Outcome of one ``sync_user`` call.

    ``projections`` is keyed by entitlement key. On a provider error it holds
    the last known projections, unchanged.
    """

    user_id: str
    projections: dict[str, UserEntitlement] = field(default_factory=dict)
    stale: bool = False
    sync_error: str | None = None
    # A newer snapshot was written while this one was in flight.
    discarded: bool = False
    # The provider's own verdict for ``standard`` in the same fetch (shadow check).
    provider_reports_active: bool | None = None

    @property
    def projection(self) -> UserEntitlement | None:
        return self.projections.get(ENTITLEMENT_STANDARD)

    @property
    def applied(self) -> bool:
        return self.sync_error is None and not self.discarded


@dataclass(frozen=True)
class _SyncPlan:
    primary_customer_id: str
    legacy_customer_ids: tuple[str, ...]
    known_customer_ids: frozenset[str]
    projections: dict[str, UserEntitlement]


class EntitlementSyncService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        provider: BillingProviderPort,
        budget: BillingProviderBudgetPort,
        access_config: EntitlementAccessConfig | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._uow_factory = uow_factory
        self._provider = provider
        self._budget = budget
        self._config = access_config or EntitlementAccessConfig()
        self._clock = clock
        self._new_id = id_factory

    @property
    def provider_name(self) -> str:
        return self._provider.provider

    async def sync_user(
        self,
        user_id: str,
        lane: str,
        reason: str,
        *,
        purchase_hint: bool = False,
        budget_timeout: float | None = None,
    ) -> EntitlementSyncResult:
        """Re-fetch the user's provider customers and rebuild their projection."""
        lane = SyncLane(lane)
        started = time.perf_counter()
        outcome = "error"
        try:
            plan = await self._load_plan(user_id)
            if plan is None:
                outcome = USER_NOT_FOUND
                return EntitlementSyncResult(user_id=user_id, sync_error=USER_NOT_FOUND)

            try:
                primary, legacy = await self._fetch(plan, lane, budget_timeout)
            except BillingProviderError as exc:
                outcome = "provider_error"
                return await self._provider_failed(
                    user_id, plan, exc, lane=lane, reason=reason, hint=purchase_hint
                )
            if legacy:
                legacy = await self.without_foreign_customers(user_id, legacy)

            result = await self._apply(
                user_id,
                primary,
                legacy,
                primary_customer_id=plan.primary_customer_id,
                reason=reason,
                purchase_hint=purchase_hint,
            )
            if result.sync_error == USER_NOT_FOUND:
                outcome = USER_NOT_FOUND
            else:
                outcome = "discarded" if result.discarded else "applied"
            return result
        finally:
            distribution_metric(
                "entitlement.sync.latency",
                (time.perf_counter() - started) * 1000.0,
                unit="millisecond",
                attributes={"lane": str(lane), "outcome": outcome},
            )

    async def recompute_user(
        self, user_id: str, *, now: datetime | None = None
    ) -> dict[str, UserEntitlement]:
        """DB-only recompute (read-time expiry, grants). Never calls the provider."""
        async with self._uow_factory() as uow:
            await uow.user_entitlements.lock_users([user_id])
            return await self.recompute_locked(uow, user_id, now=now)

    async def recompute_locked(
        self,
        uow: AsyncUnitOfWorkPort,
        user_id: str,
        *,
        now: datetime | None = None,
        provider_synced_at: datetime | None = None,
        purchase_hint_at: datetime | None = None,
    ) -> dict[str, UserEntitlement]:
        """Recompute every projection row of ``user_id`` inside ``uow``.

        The caller must already hold the user's advisory lock in this transaction.
        """
        now = now or self._clock()
        subs = await uow.store_subscriptions.list_for_user(user_id)
        grants = await uow.entitlement_grants.list_for_user(user_id)
        previous = {
            row.entitlement_key: row
            for row in await uow.user_entitlements.list_for_user(user_id)
        }
        keys = set(previous)
        keys.update(sub.entitlement_key for sub in subs if sub.entitlement_key)
        keys.update(grant.entitlement_key for grant in grants)
        if provider_synced_at is not None:
            # Only a provider fetch may create a "nothing here" standard row.
            keys.add(ENTITLEMENT_STANDARD)
        sandbox_allowed = self._config.sandbox_allowed(user_id)

        projections: dict[str, UserEntitlement] = {}
        for key in sorted(keys):
            projection = compute_projection(
                user_id,
                key,
                subs,
                grants,
                now,
                sandbox_allowed,
                previous=previous.get(key),
                provider_synced_at=provider_synced_at,
            )
            if purchase_hint_at is not None:
                projection = replace(projection, last_purchase_hint_at=purchase_hint_at)
            await uow.user_entitlements.upsert(projection)
            projections[key] = projection
        return projections

    async def _load_plan(self, user_id: str) -> _SyncPlan | None:
        async with self._uow_factory() as uow:
            user = await uow.users.find_by_id(user_id)  # type: ignore[arg-type]
            if user is None or not user.is_active or not user.firebase_uid:
                return None
            identities = await uow.billing_identities.list_for_user(
                user_id, self.provider_name
            )
            projections = {
                row.entitlement_key: row
                for row in await uow.user_entitlements.list_for_user(user_id)
            }
        primary_id = user.firebase_uid
        legacy_ids = tuple(
            identity.provider_customer_id
            for identity in identities
            if identity.kind == IdentityKind.LEGACY_LEAD
            and identity.provider_customer_id != primary_id
        )
        return _SyncPlan(
            primary_customer_id=primary_id,
            legacy_customer_ids=legacy_ids,
            known_customer_ids=frozenset(
                {primary_id, *(i.provider_customer_id for i in identities)}
            ),
            projections=projections,
        )

    async def _fetch(
        self, plan: _SyncPlan, lane: SyncLane, budget_timeout: float | None
    ) -> tuple[CustomerSnapshot, list[CustomerSnapshot]]:
        """Fetch outside any transaction. Only the primary fetch may fail the sync."""
        primary = await self._fetch_one(plan.primary_customer_id, lane, budget_timeout)
        legacy: list[CustomerSnapshot] = []
        seen = {primary.canonical_customer_id}
        for customer_id in plan.legacy_customer_ids:
            try:
                snapshot = await self._fetch_one(customer_id, lane, budget_timeout)
            except ProviderNotFound:
                continue
            except BillingProviderError as exc:
                # Its rows stay as they are; a later sync retries the customer.
                increment_metric(
                    "entitlement.sync.provider_error",
                    attributes={
                        "code": exc.code,
                        "lane": str(lane),
                        "customer": "legacy",
                    },
                )
                continue
            if snapshot.canonical_customer_id in seen:
                continue
            # Same rule as web-funnel finalize: the canonical id must be one of ours.
            if snapshot.canonical_customer_id not in (
                plan.known_customer_ids | {customer_id}
            ):
                increment_metric(
                    "entitlement.sync.legacy_rejected",
                    attributes={"reason": "canonical_mismatch"},
                )
                logger.warning(
                    "Legacy billing customer resolved to an unknown canonical id",
                    extra={"lane": str(lane)},
                )
                continue
            seen.add(snapshot.canonical_customer_id)
            legacy.append(snapshot)
        return primary, legacy

    async def _fetch_one(
        self, customer_id: str, lane: SyncLane, budget_timeout: float | None
    ) -> CustomerSnapshot:
        async with self._budget.acquire(lane, budget_timeout):
            return await self._provider.fetch_customer(customer_id)

    async def without_foreign_customers(
        self, user_id: str, legacy: Sequence[CustomerSnapshot]
    ) -> list[CustomerSnapshot]:
        """Drop legacy customers that RevenueCat also lists under another account.

        The canonical-id check cannot catch a legacy id that is itself another
        user's Firebase UID (legacy TRANSFER rows keep the source user but point
        at the target's id), nor an anonymous id another user has since logged
        in from. Such a customer is that user's: it never grants this user
        access, and its rows stay with whoever owns them.
        """
        kept: list[CustomerSnapshot] = []
        async with self._uow_factory() as uow:
            for snapshot in legacy:
                if await self._customer_of_other_user(uow, user_id, snapshot):
                    increment_metric(
                        "entitlement.sync.legacy_rejected",
                        attributes={"reason": "other_user"},
                    )
                    logger.warning(
                        "Legacy billing customer belongs to another user; skipped",
                        extra={"user_id": user_id},
                    )
                    continue
                kept.append(snapshot)
        return kept

    async def _customer_of_other_user(
        self, uow: AsyncUnitOfWorkPort, user_id: str, snapshot: CustomerSnapshot
    ) -> bool:
        customer_ids = set(snapshot.customer_ids())
        if snapshot.requested_customer_id:
            customer_ids.add(snapshot.requested_customer_id)
        for customer_id in sorted(customer_ids):
            for user in (
                await uow.users.find_by_firebase_uid(customer_id),
                await uow.users.find_deleted_by_firebase_uid(customer_id),
            ):
                if user is not None and str(user.id) != user_id:
                    return True
            identity = await uow.billing_identities.find_by_customer_id(
                self.provider_name, customer_id
            )
            if identity is not None and identity.user_id != user_id:
                return True
        return False

    async def _provider_failed(
        self,
        user_id: str,
        plan: _SyncPlan,
        exc: BillingProviderError,
        *,
        lane: SyncLane,
        reason: str,
        hint: bool,
    ) -> EntitlementSyncResult:
        increment_metric(
            "entitlement.sync.provider_error",
            attributes={"code": exc.code, "lane": str(lane), "customer": "primary"},
        )
        logger.warning(
            "Entitlement sync kept the last projection after a provider error",
            extra={
                "user_id": user_id,
                "code": exc.code,
                "lane": str(lane),
                "reason": reason,
            },
        )
        projections = plan.projections
        if hint:
            projections = await self._record_hint(user_id, plan.projections)
        return EntitlementSyncResult(
            user_id=user_id,
            projections=projections,
            stale=True,
            sync_error=exc.code,
        )

    async def record_purchase_hint(self, user_id: str) -> dict[str, UserEntitlement]:
        """Mark a purchase hint without a provider fetch (rate-limited ``/sync``),
        so a negative answer in the hint window is still never cached."""
        async with self._uow_factory() as uow:
            projections = {
                row.entitlement_key: row
                for row in await uow.user_entitlements.list_for_user(user_id)
            }
        if not projections:
            return projections
        return await self._record_hint(user_id, projections)

    async def _record_hint(
        self, user_id: str, projections: dict[str, UserEntitlement]
    ) -> dict[str, UserEntitlement]:
        at = self._clock()
        async with self._uow_factory() as uow:
            await uow.user_entitlements.lock_users([user_id])
            for key in projections:
                await uow.user_entitlements.set_last_purchase_hint_at(
                    user_id, key, at=at
                )
        return {
            key: replace(row, last_purchase_hint_at=at)
            for key, row in projections.items()
        }

    async def _apply(
        self,
        user_id: str,
        primary: CustomerSnapshot,
        legacy: Sequence[CustomerSnapshot],
        *,
        primary_customer_id: str,
        reason: str,
        purchase_hint: bool,
    ) -> EntitlementSyncResult:
        provider = self.provider_name
        snapshots = [primary, *legacy]
        fetch_started_at = primary.fetched_at
        incoming_keys = _keys(snapshots)

        async with self._uow_factory() as uow:
            # Lock order: subscription keys, then users in user_id order. Under
            # the key locks no other sync can move these rows, so the owners read
            # here are final and every affected user is locked together.
            await uow.store_subscriptions.lock_keys(provider, incoming_keys)
            owners = {
                row.user_id
                for row in await uow.store_subscriptions.get_by_keys(
                    provider, incoming_keys
                )
            }
            await uow.user_entitlements.lock_users(sorted({user_id, *owners}))
            # Account deletion takes the same lock (D11): a user deleted while
            # the provider answered gets no identities, rows or projection.
            user = await uow.users.find_by_id(user_id)  # type: ignore[arg-type]
            if user is None or not user.is_active:
                logger.info(
                    "Entitlement sync dropped: user deleted during the fetch",
                    extra={"user_id": user_id, "reason": reason},
                )
                return EntitlementSyncResult(user_id=user_id, sync_error=USER_NOT_FOUND)

            now = self._clock()
            current = {
                row.entitlement_key: row
                for row in await uow.user_entitlements.list_for_user(user_id)
            }
            last_synced = max(
                (
                    row.last_provider_sync_at
                    for row in current.values()
                    if row.last_provider_sync_at is not None
                ),
                default=None,
            )
            if last_synced is not None and fetch_started_at <= last_synced:
                increment_metric(
                    "entitlement.sync.discarded", attributes={"reason": reason}
                )
                if purchase_hint:
                    for key in current:
                        await uow.user_entitlements.set_last_purchase_hint_at(
                            user_id, key, at=now
                        )
                    current = {
                        key: replace(row, last_purchase_hint_at=now)
                        for key, row in current.items()
                    }
                return EntitlementSyncResult(
                    user_id=user_id,
                    projections=current,
                    discarded=True,
                    provider_reports_active=primary.provider_reports_active(now=now),
                )

            # Re-read under the user locks: an owner's own sync may have marked
            # one of these rows absent meanwhile.
            existing = await uow.store_subscriptions.get_by_keys(
                provider, incoming_keys
            )
            changed, previous_owners = await self._replace_snapshots(
                uow, user_id, snapshots, existing, now=now
            )
            if changed:
                await uow.store_subscriptions.upsert_many(changed)

            await self._link_identities(
                uow, user_id, primary_customer_id, snapshots, now=now
            )

            projections = await self.recompute_locked(
                uow,
                user_id,
                now=now,
                provider_synced_at=fetch_started_at,
                purchase_hint_at=now if purchase_hint else None,
            )
            for owner in sorted(previous_owners - {user_id}):
                # The owner lost a subscription to this user (transfer/alias).
                await self.recompute_locked(uow, owner, now=now)

        provider_active = primary.provider_reports_active(now=now)
        _report_mismatch(projections.get(ENTITLEMENT_STANDARD), provider_active)
        return EntitlementSyncResult(
            user_id=user_id,
            projections=projections,
            provider_reports_active=provider_active,
        )

    async def _replace_snapshots(
        self,
        uow: AsyncUnitOfWorkPort,
        user_id: str,
        snapshots: Sequence[CustomerSnapshot],
        existing: Iterable[StoreSubscription],
        *,
        now: datetime,
    ) -> tuple[list[StoreSubscription], set[str]]:
        """Snapshot replace (plan §5): upsert listed rows, mark unlisted ones absent."""
        provider = self.provider_name
        by_key = {(row.environment, row.subscription_key): row for row in existing}
        changed: dict[_SUBSCRIPTION_KEY, StoreSubscription] = {}
        previous_owners: set[str] = set()

        for index, snapshot in enumerate(snapshots):
            is_primary = index == 0
            listed: set[_SUBSCRIPTION_KEY] = set()
            for sub in snapshot.subscriptions:
                key = (sub.environment, sub.subscription_key)
                if key in listed or key in changed:
                    continue
                listed.add(key)
                row = by_key.get(key)
                if row is not None and row.user_id != user_id and not is_primary:
                    # Primary beats legacy: only the customer a user signs in
                    # as may take a row from another user.
                    increment_metric("entitlement.sync.legacy_row_kept")
                    continue
                if row is None:
                    changed[key] = StoreSubscription.from_snapshot(
                        sub,
                        row_id=self._new_id(),
                        user_id=user_id,
                        provider=provider,
                        fetched_at=snapshot.fetched_at,
                        now=now,
                    )
                    continue
                if not row.accepts_snapshot(snapshot.fetched_at):
                    increment_metric("entitlement.sync.stale_row")
                    continue
                if row.user_id != user_id:
                    previous_owners.add(row.user_id)
                    logger.info(
                        "Store subscription moved to the user whose customer lists it",
                        extra={"from_user_id": row.user_id, "to_user_id": user_id},
                    )
                changed[key] = row.refreshed_from(
                    sub, user_id=user_id, fetched_at=snapshot.fetched_at, now=now
                )

            # Rows this customer used to list but no longer does (transfer etc.).
            scope = set(snapshot.customer_ids())
            if snapshot.requested_customer_id:
                scope.add(snapshot.requested_customer_id)
            for customer_id in sorted(scope):
                for row in await uow.store_subscriptions.list_for_customer(
                    provider, customer_id, user_id=user_id
                ):
                    key = (row.environment, row.subscription_key)
                    if (
                        key in listed
                        or key in changed
                        or row.state == StoreSubscriptionState.ABSENT
                        or not row.accepts_snapshot(snapshot.fetched_at)
                    ):
                        continue
                    changed[key] = row.marked_absent(
                        fetched_at=snapshot.fetched_at, now=now
                    )
        return list(changed.values()), previous_owners

    async def _link_identities(
        self,
        uow: AsyncUnitOfWorkPort,
        user_id: str,
        primary_id: str,
        snapshots: Sequence[CustomerSnapshot],
        *,
        now: datetime,
    ) -> None:
        """Seed the primary identity (``users.firebase_uid``) and record
        aliases. Never fails the sync."""
        provider = self.provider_name
        outcome = await uow.billing_identities.upsert_primary(
            user_id, provider, primary_id, seen_at=now
        )
        record_primary_link_outcome(outcome, user_id=user_id, provider=provider)
        for snapshot in snapshots:
            for customer_id in snapshot.customer_ids():
                if customer_id == primary_id:
                    continue
                kind = (
                    IdentityKind.ANONYMOUS
                    if customer_id.startswith(_ANONYMOUS_ID_PREFIX)
                    else IdentityKind.ALIAS
                )
                alias_outcome = await uow.billing_identities.add_alias(
                    user_id, provider, customer_id, kind, seen_at=now
                )
                if alias_outcome == IdentityLinkOutcome.CONFLICT:
                    increment_metric("identity.alias_conflict")
                    logger.warning(
                        "Billing alias already belongs to another user; skipped",
                        extra={"user_id": user_id, "provider": provider},
                    )


def record_primary_link_outcome(
    outcome: IdentityLinkOutcome, *, user_id: str, provider: str
) -> None:
    """Metrics and logs for ``upsert_primary`` (sync and ``/v1/users/sync``)."""
    if outcome == IdentityLinkOutcome.CONFLICT:
        increment_metric("identity.primary_conflict")
        logger.warning(
            "Primary billing identity belongs to another user",
            extra={"user_id": user_id, "provider": provider},
        )
    elif outcome == IdentityLinkOutcome.RECLAIMED:
        increment_metric("identity.primary_reclaimed")
    elif outcome == IdentityLinkOutcome.RETIRED:
        increment_metric("identity.primary_retired")
        logger.warning(
            "Primary billing identity is retired (deleted account); not relinked",
            extra={"user_id": user_id, "provider": provider},
        )


def _keys(snapshots: Iterable[CustomerSnapshot]) -> list[_SUBSCRIPTION_KEY]:
    keys: dict[_SUBSCRIPTION_KEY, None] = {}
    for snapshot in snapshots:
        for sub in snapshot.subscriptions:
            keys[(sub.environment, sub.subscription_key)] = None
    return list(keys)


def _report_mismatch(projection: UserEntitlement | None, provider_active: bool) -> None:
    """Shadow check (plan §9.3): our verdict vs RevenueCat's in the same fetch."""
    ours = projection is not None and projection.has_access
    if ours == provider_active:
        return
    direction = (
        "provider_active_ledger_inactive"
        if provider_active
        else ("ledger_active_provider_inactive")
    )
    increment_metric("entitlement.mismatch", attributes={"direction": direction})
