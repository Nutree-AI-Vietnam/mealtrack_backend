"""Entitlement ledger backfill (NM-680, plan §9.2).

Runs the production ``EntitlementSyncService`` on the backfill lane (lowest RC
priority). It has no side effects beyond the ledger tables: no queue events,
referral credit or notifications. ``dry_run`` fetches and compares only: it
reads the ledger and writes nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.app.services.entitlement_sync_service import (
    EntitlementAccessConfig,
    EntitlementSyncService,
)
from src.domain.exceptions.billing_exceptions import BillingProviderError
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    IdentityKind,
    SyncLane,
)
from src.domain.model.billing.ledger import StoreSubscription
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.billing_provider_budget_port import BillingProviderBudgetPort
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome
from src.domain.services.billing.access_policy import (
    compute_projection,
    effective_has_access,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

BACKFILL_REASON = "backfill"
_ANONYMOUS_ID_PREFIX = "$RCAnonymousID:"


@dataclass(frozen=True)
class BackfillCandidate:
    """One active user and the provider ids the legacy tables know for them."""

    user_id: str
    firebase_uid: str
    # Lead UUIDs / $RCAnonymousID values from users.revenuecat_customer_id and
    # subscriptions.revenuecat_subscriber_id: known RC customers only, because
    # RC v1 GET is get-or-create.
    legacy_customer_ids: tuple[str, ...] = ()
    # The legacy ``subscriptions`` table's verdict, for the diff report.
    legacy_active: bool | None = None


@dataclass
class BackfillReportRow:
    user_id: str
    outcome: str  # match | mismatch | provider_error | user_not_found | discarded
    rc_active: bool | None = None
    ledger_before: bool | None = None
    ledger_after: bool | None = None
    legacy_active: bool | None = None
    state_after: str | None = None
    sync_error: str | None = None
    legacy_customer_ids: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_seedable_legacy_id(customer_id: str) -> bool:
    """Only ids no account signs in with: ``$RCAnonymousID:*`` and lead UUIDs.

    Legacy ``subscriptions`` rows also hold other users' Firebase UIDs: a
    TRANSFER rewrites ``revenuecat_subscriber_id`` to the target's id but keeps
    ``user_id`` on the source user. Seeding such an id would hand the target's
    purchase and webhooks to the source user.
    """
    if customer_id.startswith(_ANONYMOUS_ID_PREFIX):
        return len(customer_id) > len(_ANONYMOUS_ID_PREFIX)
    try:
        return str(uuid.UUID(customer_id)) == customer_id.lower()
    except (ValueError, AttributeError, TypeError):
        return False


def legacy_ids_for(candidate: BackfillCandidate) -> list[str]:
    return [
        customer_id
        for customer_id in dict.fromkeys(candidate.legacy_customer_ids)
        if customer_id
        and customer_id != candidate.firebase_uid
        and is_seedable_legacy_id(customer_id)
    ]


class EntitlementBackfillService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        sync_service: EntitlementSyncService,
        provider: BillingProviderPort,
        budget: BillingProviderBudgetPort,
        access_config: EntitlementAccessConfig | None = None,
        budget_timeout: float | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._sync = sync_service
        self._provider = provider
        self._budget = budget
        self._config = access_config or EntitlementAccessConfig()
        self._budget_timeout = budget_timeout
        self._clock = clock

    async def backfill(
        self, candidate: BackfillCandidate, *, dry_run: bool
    ) -> BackfillReportRow:
        if dry_run:
            return await self._dry_run(candidate)
        return await self._apply(candidate)

    async def _apply(self, candidate: BackfillCandidate) -> BackfillReportRow:
        legacy_ids, notes = await self._own_legacy_ids(candidate)
        before = await self._ledger_has_access(candidate.user_id)
        if legacy_ids:
            notes += await self._seed_legacy_identities(candidate.user_id, legacy_ids)
        result = await self._sync.sync_user(
            candidate.user_id,
            SyncLane.BACKFILL,
            BACKFILL_REASON,
            budget_timeout=self._budget_timeout,
        )
        projection = result.projection
        after = effective_has_access(projection, self._clock()) if projection else None
        row = BackfillReportRow(
            user_id=candidate.user_id,
            outcome="",
            rc_active=result.provider_reports_active,
            ledger_before=before,
            ledger_after=after,
            legacy_active=candidate.legacy_active,
            state_after=projection.state if projection else None,
            sync_error=result.sync_error,
            legacy_customer_ids=len(legacy_ids),
            notes=notes,
        )
        if result.sync_error:
            row.outcome = (
                result.sync_error
                if result.sync_error == "user_not_found"
                else "provider_error"
            )
        elif result.discarded:
            row.outcome = "discarded"
        else:
            row.outcome = _compare(row)
        return row

    async def _dry_run(self, candidate: BackfillCandidate) -> BackfillReportRow:
        legacy_ids, notes = await self._own_legacy_ids(candidate)
        before = await self._ledger_has_access(candidate.user_id)
        row = BackfillReportRow(
            user_id=candidate.user_id,
            outcome="",
            ledger_before=before,
            legacy_active=candidate.legacy_active,
            legacy_customer_ids=len(legacy_ids),
            notes=notes,
        )
        try:
            primary = await self._fetch(candidate.firebase_uid)
        except BillingProviderError as exc:
            row.outcome, row.sync_error = "provider_error", exc.code
            return row
        snapshots = [primary]
        known = {candidate.firebase_uid, *legacy_ids}
        for customer_id in legacy_ids:
            try:
                snapshot = await self._fetch(customer_id)
            except BillingProviderError as exc:
                row.notes.append(f"legacy_fetch_failed:{exc.code}")
                continue
            if snapshot.canonical_customer_id not in known | {
                primary.canonical_customer_id
            }:
                row.notes.append("legacy_rejected")
                continue
            if not await self._sync.without_foreign_customers(
                candidate.user_id, [snapshot]
            ):
                row.notes.append("legacy_rejected:other_user")
                continue
            snapshots.append(snapshot)

        now = self._clock()
        async with self._uow_factory() as uow:
            grants = await uow.entitlement_grants.list_for_user(candidate.user_id)
        projection = compute_projection(
            candidate.user_id,
            ENTITLEMENT_STANDARD,
            _snapshot_rows(candidate.user_id, snapshots, self._provider.provider, now),
            grants,
            now,
            self._config.sandbox_allowed(candidate.user_id),
            provider_synced_at=primary.fetched_at,
        )
        row.rc_active = any(
            snapshot.provider_reports_active(now=now) for snapshot in snapshots
        )
        row.ledger_after = effective_has_access(projection, now)
        row.state_after = projection.state
        row.outcome = _compare(row)
        return row

    async def _fetch(self, customer_id: str) -> CustomerSnapshot:
        async with self._budget.acquire(SyncLane.BACKFILL, self._budget_timeout):
            return await self._provider.fetch_customer(customer_id)

    async def _ledger_has_access(self, user_id: str) -> bool | None:
        async with self._uow_factory() as uow:
            projection = await uow.user_entitlements.get(user_id)
        if projection is None:
            return None
        return effective_has_access(projection, self._clock())

    async def _own_legacy_ids(
        self, candidate: BackfillCandidate
    ) -> tuple[list[str], list[str]]:
        """Seedable legacy ids minus any that are some account's Firebase UID.

        ``load_candidates`` already filters them; this re-check keeps a
        hand-built candidate from linking another user's sign-in id.
        """
        offered = {
            customer_id
            for customer_id in candidate.legacy_customer_ids
            if customer_id and customer_id != candidate.firebase_uid
        }
        kept: list[str] = []
        async with self._uow_factory() as uow:
            for customer_id in legacy_ids_for(candidate):
                if (
                    await uow.users.find_by_firebase_uid(customer_id) is None
                    and await uow.users.find_deleted_by_firebase_uid(customer_id)
                    is None
                ):
                    kept.append(customer_id)
        skipped = len(offered) - len(kept)
        return kept, [f"legacy_ids_skipped:{skipped}"] if skipped else []

    async def _seed_legacy_identities(
        self, user_id: str, customer_ids: Sequence[str]
    ) -> list[str]:
        """Legacy customers are fetched on every sync (accepted only when their
        canonical id is one of the user's). A conflict never fails the run."""
        now = self._clock()
        conflicts = 0
        async with self._uow_factory() as uow:
            for customer_id in customer_ids:
                outcome = await uow.billing_identities.add_alias(
                    user_id,
                    self._provider.provider,
                    customer_id,
                    IdentityKind.LEGACY_LEAD,
                    seen_at=now,
                )
                conflicts += outcome == IdentityLinkOutcome.CONFLICT
        return [f"legacy_id_conflicts:{conflicts}"] if conflicts else []


def _snapshot_rows(
    user_id: str,
    snapshots: Sequence[CustomerSnapshot],
    provider: str,
    now: datetime,
) -> list[StoreSubscription]:
    rows: dict[tuple[str, str], StoreSubscription] = {}
    for snapshot in snapshots:
        for sub in snapshot.subscriptions:
            key = (sub.environment, sub.subscription_key)
            if key in rows:
                continue
            rows[key] = StoreSubscription.from_snapshot(
                sub,
                row_id=str(uuid.uuid4()),
                user_id=user_id,
                provider=provider,
                fetched_at=snapshot.fetched_at,
                now=now,
            )
    return list(rows.values())


def _compare(row: BackfillReportRow) -> str:
    """``match`` when our verdict equals RevenueCat's own in the same fetch."""
    if row.legacy_active is not None and row.legacy_active != row.ledger_after:
        row.notes.append("legacy_differs")
    if row.rc_active is None or row.ledger_after is None:
        return "mismatch" if row.rc_active else "match"
    return "match" if row.rc_active == row.ledger_after else "mismatch"
