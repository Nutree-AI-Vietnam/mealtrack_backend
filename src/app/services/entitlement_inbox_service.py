"""Entitlement inbox: provider webhooks -> ``billing_provider_events`` -> sync.

The inbox row is a job, never a source of state (NM-680, plan §6.1): processing
a row resolves the users it concerns and re-fetches them through
``EntitlementSyncService``, so duplicate and out-of-order deliveries converge
on the same projection. The webhook route records rows inside its legacy unit
of work and processes them inline after commit; the worker processes retries
and replays parked rows.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.app.services.entitlement_sync_service import (
    USER_NOT_FOUND,
    EntitlementSyncService,
    record_primary_link_outcome,
)
from src.domain.model.billing.constants import (
    INBOX_MAX_ATTEMPTS,
    PROVIDER_REVENUECAT,
    IdentityKind,
    ParkReason,
    ProviderEventStatus,
    SyncLane,
)
from src.domain.model.billing.ledger import (
    BillingCustomerIdentity,
    BillingProviderEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome
from src.domain.utils.timezone_utils import utc_now
from src.observability import increment_metric

logger = logging.getLogger(__name__)

PROVIDER_EVENT_ID_MAX_LENGTH = 255
HASHED_EVENT_ID_PREFIX = "sha256:"
# RevenueCat dashboard test deliveries carry no customer state.
IGNORED_EVENT_TYPES = frozenset({"TEST"})
TRANSFER_EVENT = "TRANSFER"
RETRY_BASE_DELAY = timedelta(seconds=30)
# A new webhook row is due for the worker only after this, so the worker picks
# up what the inline sync (3 s budget) did not finish instead of racing it.
INLINE_SYNC_GRACE = timedelta(seconds=30)
RETRY_MAX_DELAY = timedelta(hours=6)
LAST_ERROR_MAX_LENGTH = 500

# Outcomes of ``process``.
OUTCOME_PROCESSED = "processed"
OUTCOME_RETRY = "retry"
OUTCOME_DEAD = "dead"
OUTCOME_UNMATCHED = "unmatched"
OUTCOME_IGNORED = "ignored"
OUTCOME_SETTLED = "settled"
OUTCOME_MISSING = "missing"
OUTCOME_LEASED = "leased"


def revenuecat_provider_event_id(event: Mapping[str, Any], raw_body: bytes) -> str:
    """RC ``event.id``, or ``sha256:<hex>`` of the raw body when it has none."""
    event_id = event.get("id")
    if isinstance(event_id, str) and event_id.strip():
        return event_id.strip()[:PROVIDER_EVENT_ID_MAX_LENGTH]
    return HASHED_EVENT_ID_PREFIX + hashlib.sha256(raw_body).hexdigest()


def strip_subscriber_attributes(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of the webhook body without ``subscriber_attributes`` (D9: ad ids, IP)."""
    stored = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key != "subscriber_attributes"
    }
    event = stored.get("event")
    if isinstance(event, dict):
        event.pop("subscriber_attributes", None)
    return stored


def candidate_customer_ids(event: Mapping[str, Any]) -> list[str]:
    """Provider ids in a webhook, in the legacy lookup order, de-duplicated."""
    candidates = [
        event.get("app_user_id"),
        event.get("original_app_user_id"),
        *_id_list(event.get("aliases")),
        *_id_list(event.get("redeemed_by")),
        *_id_list(event.get("transferred_to")),
        *_id_list(event.get("transferred_from")),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str) and candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff for the ``attempts``-th failure (0-based), capped."""
    exponent = min(max(attempts, 0), 20)
    return min(RETRY_BASE_DELAY * (2**exponent), RETRY_MAX_DELAY)


def _id_list(raw: Any) -> list[Any]:
    return list(raw) if isinstance(raw, list | tuple) else []


def _timestamp_ms(raw: Any) -> datetime | None:
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return None
    try:
        return datetime.fromtimestamp(raw / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _bounded(raw: Any, length: int) -> str | None:
    return raw[:length] if isinstance(raw, str) and raw else None


@dataclass(frozen=True)
class RecordedProviderEvent:
    """Result of recording one delivery in the inbox."""

    event_id: str
    inserted: bool
    status: str

    @property
    def settled(self) -> bool:
        """A duplicate of a settled row is acknowledged without any work."""
        return self.status in (
            ProviderEventStatus.PROCESSED,
            ProviderEventStatus.IGNORED,
        )


@dataclass(frozen=True)
class EventUserResolution:
    """Active users a provider event concerns; the first one is its subject."""

    user_ids: tuple[str, ...] = ()
    park_reason: str | None = None

    @property
    def primary_user_id(self) -> str | None:
        return self.user_ids[0] if self.user_ids else None


class EntitlementInboxService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        sync_service: EntitlementSyncService,
        provider: str = PROVIDER_REVENUECAT,
        max_attempts: int = INBOX_MAX_ATTEMPTS,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._uow_factory = uow_factory
        self._sync = sync_service
        self._provider = provider
        self._max_attempts = max_attempts
        self._clock = clock
        self._new_id = id_factory

    # ---- recording (inside the caller's unit of work) ----

    def build_event(
        self, payload: Mapping[str, Any], raw_body: bytes
    ) -> BillingProviderEvent:
        event = payload.get("event")
        event = event if isinstance(event, Mapping) else {}
        received_at = self._clock()
        return BillingProviderEvent(
            id=self._new_id(),
            provider=self._provider,
            provider_event_id=revenuecat_provider_event_id(event, raw_body),
            event_type=_bounded(event.get("type"), 64) or "UNKNOWN",
            status=ProviderEventStatus.RECEIVED,
            received_at=received_at,
            next_attempt_at=received_at + INLINE_SYNC_GRACE,
            environment=_bounded(event.get("environment"), 16),
            app_user_id=_bounded(event.get("app_user_id"), 255),
            event_ts=_timestamp_ms(event.get("event_timestamp_ms")),
            raw=strip_subscriber_attributes(payload),
        )

    async def record(
        self,
        uow: AsyncUnitOfWorkPort,
        payload: Mapping[str, Any],
        raw_body: bytes,
    ) -> RecordedProviderEvent:
        """``INSERT ... ON CONFLICT (provider, provider_event_id) DO NOTHING``."""
        event = self.build_event(payload, raw_body)
        if await uow.billing_events.insert_if_absent(event):
            return RecordedProviderEvent(event.id, inserted=True, status=event.status)
        existing = await uow.billing_events.get_by_provider_event_id(
            event.provider, event.provider_event_id
        )
        if existing is None:  # pragma: no cover - conflicting row vanished
            return RecordedProviderEvent(event.id, inserted=False, status="missing")
        return RecordedProviderEvent(
            existing.id, inserted=False, status=existing.status
        )

    async def park(self, uow: AsyncUnitOfWorkPort, event_id: str, reason: str) -> None:
        # A deleted account's deliveries are never replayed: keep no body (D11).
        await uow.billing_events.park(
            event_id, reason=reason, drop_raw=reason == ParkReason.DELETED_USER
        )
        increment_metric("entitlement.inbox.parked", attributes={"reason": reason})

    async def attach_user(
        self, uow: AsyncUnitOfWorkPort, event_id: str, user_id: str
    ) -> None:
        """Remember the user the webhook route matched, which may be only via
        the legacy ``subscriptions`` fallback this service does not repeat.

        A row parked earlier (``unmatched``) whose redelivery now matches a
        user is requeued, so the inline sync processes it instead of treating
        it as settled. It stays due after the inline grace, like a new row.
        """
        await uow.billing_events.set_user(event_id, user_id)
        due_at = self._clock() + INLINE_SYNC_GRACE
        if await uow.billing_events.requeue([event_id], user_id=user_id, now=due_at):
            increment_metric(
                "entitlement.inbox.replayed", attributes={"trigger": "redelivery"}
            )

    # ---- user resolution ----

    async def resolve_users(
        self, uow: AsyncUnitOfWorkPort, event: Mapping[str, Any]
    ) -> EventUserResolution:
        """Active users only; per id, ``billing_customer_identities`` first.

        TRANSFER events also resolve every ``transferred_from`` and
        ``transferred_to`` id, so both sides are re-synced.
        """
        saw_deleted = False
        user_ids: list[str] = []
        for customer_id in candidate_customer_ids(event):
            user_id, deleted = await self._resolve_one(uow, customer_id)
            saw_deleted = saw_deleted or deleted
            if user_id:
                user_ids.append(user_id)
                break
        if event.get("type") == TRANSFER_EVENT:
            for customer_id in [
                *_id_list(event.get("transferred_from")),
                *_id_list(event.get("transferred_to")),
            ]:
                if not isinstance(customer_id, str) or not customer_id:
                    continue
                user_id, deleted = await self._resolve_one(uow, customer_id)
                saw_deleted = saw_deleted or deleted
                if user_id and user_id not in user_ids:
                    user_ids.append(user_id)
        if user_ids:
            return EventUserResolution(user_ids=tuple(user_ids))
        reason = ParkReason.DELETED_USER if saw_deleted else ParkReason.USER_NOT_FOUND
        return EventUserResolution(park_reason=reason)

    async def _resolve_one(
        self, uow: AsyncUnitOfWorkPort, customer_id: str
    ) -> tuple[str | None, bool]:
        """``(active user id, whether a deleted account matched)``.

        Order: a primary identity, then an exact ``users.firebase_uid``, then
        any other identity kind. A user's own Firebase UID always beats an
        alias or legacy-lead row another user may hold for it.
        """
        deleted = False
        identity = await uow.billing_identities.find_by_customer_id(
            self._provider, customer_id, include_retired=True
        )
        if identity is not None and identity.kind == IdentityKind.PRIMARY:
            user_id, deleted = await self._identity_user(uow, identity)
            if user_id:
                return user_id, False
        user = await uow.users.find_by_firebase_uid(customer_id)
        if user is not None and user.is_active:
            return str(user.id), False
        if identity is not None and identity.kind != IdentityKind.PRIMARY:
            user_id, deleted = await self._identity_user(uow, identity)
            if user_id:
                return user_id, False
        try:
            internal_id = str(uuid.UUID(customer_id))
        except (ValueError, AttributeError, TypeError):
            internal_id = None
        if internal_id is not None:
            user = await uow.users.find_by_id(internal_id)  # type: ignore[arg-type]
            if user is not None and user.is_active:
                return str(user.id), False
        if not deleted:
            deleted = (
                await uow.users.find_deleted_by_firebase_uid(customer_id) is not None
            )
        return None, deleted

    async def _identity_user(
        self, uow: AsyncUnitOfWorkPort, identity: BillingCustomerIdentity
    ) -> tuple[str | None, bool]:
        """``(active owner id, whether the identity is retired or inactive)``."""
        if identity.retired_at is None:
            user = await uow.users.find_by_id(identity.user_id)  # type: ignore[arg-type]
            if user is not None and user.is_active:
                return str(user.id), False
        return None, True

    # ---- processing (own transactions; never raises) ----

    async def process(self, event_id: str, *, lane: str) -> str:
        """Process one inbox row by id (webhook inline path)."""
        async with self._uow_factory() as uow:
            event = await uow.billing_events.get(event_id)
        if event is None:
            return OUTCOME_MISSING
        if event.is_settled or event.status == ProviderEventStatus.UNMATCHED:
            return OUTCOME_SETTLED
        if (
            event.status == ProviderEventStatus.PROCESSING
            and event.lease_expires_at is not None
            and event.lease_expires_at > self._clock()
        ):
            # The worker holds it (a retry of this delivery): one fetch is enough.
            return OUTCOME_LEASED
        return await self.process_claimed(event, lane=lane)

    async def process_claimed(self, event: BillingProviderEvent, *, lane: str) -> str:
        """Re-sync every user the event concerns, then settle or schedule the row."""
        try:
            return await self._process(event, SyncLane(lane))
        except Exception as exc:
            logger.exception(
                "Entitlement inbox row failed",
                extra={"event_row_id": event.id, "event_type": event.event_type},
            )
            return await self._schedule_retry(event, f"{type(exc).__name__}: {exc}")

    async def _process(self, event: BillingProviderEvent, lane: SyncLane) -> str:
        if event.event_type in IGNORED_EVENT_TYPES:
            async with self._uow_factory() as uow:
                await uow.billing_events.mark_ignored(event.id, reason="test_event")
            return OUTCOME_IGNORED

        payload = _event_payload(event)
        async with self._uow_factory() as uow:
            resolution = await self.resolve_users(uow, payload)
            if not resolution.user_ids and event.user_id:
                resolution = await self._attached_user(uow, event.user_id)
            if not resolution.user_ids:
                await self.park(
                    uow, event.id, resolution.park_reason or ParkReason.USER_NOT_FOUND
                )
        if not resolution.user_ids:
            return OUTCOME_UNMATCHED

        reason = f"event:{event.event_type}"
        errors: set[str] = set()
        synced: list[str] = []
        for user_id in resolution.user_ids:
            result = await self._sync.sync_user(user_id, lane, reason)
            if result.sync_error == USER_NOT_FOUND:
                continue
            if result.sync_error:
                errors.add(result.sync_error)
            else:
                synced.append(user_id)
        if errors:
            return await self._schedule_retry(event, ",".join(sorted(errors)))
        if not synced:
            # Every resolved user was deactivated before the sync ran.
            async with self._uow_factory() as uow:
                await self.park(uow, event.id, ParkReason.DELETED_USER)
            return OUTCOME_UNMATCHED
        async with self._uow_factory() as uow:
            await uow.billing_events.mark_processed(
                event.id, processed_at=self._clock(), user_id=resolution.primary_user_id
            )
        return OUTCOME_PROCESSED

    async def _attached_user(
        self, uow: AsyncUnitOfWorkPort, user_id: str
    ) -> EventUserResolution:
        user = await uow.users.find_by_id(user_id)  # type: ignore[arg-type]
        if user is not None and user.is_active:
            return EventUserResolution(user_ids=(str(user.id),))
        return EventUserResolution(park_reason=ParkReason.DELETED_USER)

    async def schedule_retry(self, event_id: str, error: str) -> str:
        """Retry a row whose inline processing was cut short (timeout)."""
        async with self._uow_factory() as uow:
            event = await uow.billing_events.get(event_id)
        if event is None or event.is_settled:
            return OUTCOME_SETTLED
        return await self._schedule_retry(event, error)

    async def _schedule_retry(self, event: BillingProviderEvent, error: str) -> str:
        error = error[:LAST_ERROR_MAX_LENGTH]
        async with self._uow_factory() as uow:
            if event.attempts + 1 >= self._max_attempts:
                await uow.billing_events.mark_dead(event.id, error=error)
                outcome = OUTCOME_DEAD
            else:
                await uow.billing_events.mark_retry(
                    event.id,
                    error=error,
                    next_attempt_at=self._clock() + retry_delay(event.attempts),
                )
                outcome = OUTCOME_RETRY
        increment_metric(
            "entitlement.inbox.failed",
            attributes={"outcome": outcome, "event_type": event.event_type},
        )
        return outcome

    # ---- identities and parked rows ----

    async def link_primary_identity(
        self, user_id: str, customer_id: str
    ) -> IdentityLinkOutcome:
        """Seed or refresh the user's primary identity (``/v1/users/sync``)."""
        async with self._uow_factory() as uow:
            outcome = await uow.billing_identities.upsert_primary(
                user_id, self._provider, customer_id, seen_at=self._clock()
            )
        record_primary_link_outcome(outcome, user_id=user_id, provider=self._provider)
        return outcome

    async def replay_unmatched_for_user(self, user_id: str, *, limit: int = 50) -> int:
        """Requeue parked rows whose ``app_user_id`` is one of the user's ids."""
        async with self._uow_factory() as uow:
            user = await uow.users.find_by_id(user_id)  # type: ignore[arg-type]
            if user is None or not user.is_active:
                return 0
            identities = await uow.billing_identities.list_for_user(
                user_id, self._provider
            )
            customer_ids = sorted(
                {
                    *([user.firebase_uid] if user.firebase_uid else []),
                    *(identity.provider_customer_id for identity in identities),
                }
            )
            rows = await uow.billing_events.list_unmatched(
                limit=limit, app_user_ids=customer_ids
            )
            replayed = await uow.billing_events.requeue(
                [row.id for row in rows], user_id=user_id, now=self._clock()
            )
        if replayed:
            increment_metric(
                "entitlement.inbox.replayed",
                value=replayed,
                attributes={"trigger": "user_sync"},
            )
        return replayed

    async def sweep_unmatched(
        self, *, limit: int, after: tuple[datetime, str] | None = None
    ) -> tuple[int, tuple[datetime, str] | None]:
        """Requeue parked rows whose user now resolves.

        Returns ``(requeued, cursor)``; the cursor is None once the sweep has
        reached the newest parked row.
        """
        requeued = 0
        async with self._uow_factory() as uow:
            rows = await uow.billing_events.list_unmatched(limit=limit, after=after)
            for row in rows:
                resolution = await self.resolve_users(uow, _event_payload(row))
                if resolution.primary_user_id is None:
                    continue
                requeued += await uow.billing_events.requeue(
                    [row.id], user_id=resolution.primary_user_id, now=self._clock()
                )
        if requeued:
            increment_metric(
                "entitlement.inbox.replayed",
                value=requeued,
                attributes={"trigger": "sweep"},
            )
        cursor = (rows[-1].received_at, rows[-1].id) if len(rows) >= limit else None
        return requeued, cursor


def _event_payload(event: BillingProviderEvent) -> Mapping[str, Any]:
    raw_event = (event.raw or {}).get("event")
    if isinstance(raw_event, Mapping):
        return raw_event
    # Raw payload purged (D9): the stored subject id is all that is left.
    return {"type": event.event_type, "app_user_id": event.app_user_id}
