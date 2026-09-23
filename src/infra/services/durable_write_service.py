"""Claim-before-create store for exact-replay durable mutation responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from src.domain.utils.fingerprint_utils import (
    canonicalize_fingerprint as canonicalize_fingerprint,
)
from src.domain.utils.fingerprint_utils import (
    normalize_idempotency_key as normalize_idempotency_key,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.durable_write_record import DurableWriteRecordORM
from src.infra.database.uow_async import AsyncUnitOfWork

RETENTION_DAYS = 14
PENDING_RESPONSE_STATUS = 0
MANUAL_MEAL_CREATE_ACTION = "manual_meal_create"

_REQUIRED_DURABLE_WRITE_COLUMNS = {
    "food_item": {
        "source_kind",
        "source_food_id",
        "nutrition_contract_version",
        "source_snapshot",
    },
    "meal_write_operation": {
        "user_id",
        "operation",
        "idempotency_key",
        "request_fingerprint",
        "status",
        "lease_owner",
        "lease_generation",
        "lease_expires_at",
        "target_meal_id",
        "response",
    },
}


class DurableWriteConflictError(Exception):
    """Same idempotency key was reused with a different request fingerprint."""


class DurableWriteInProgressError(Exception):
    """Same key + fingerprint is already claimed by an in-flight request."""


def _durable_write_schema_is_ready(rows) -> bool:
    available: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        available.setdefault(table_name, set()).add(column_name)
    return all(
        required.issubset(available.get(table_name, set()))
        for table_name, required in _REQUIRED_DURABLE_WRITE_COLUMNS.items()
    )


async def durable_write_schema_is_ready() -> bool:
    """Return whether storage for the v2 durable-write contract is available."""
    async with AsyncUnitOfWork() as uow:
        session = uow.session
        if session is None:
            raise RuntimeError("database session is unavailable")
        result = await session.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND (
                    (table_name = 'food_item' AND column_name IN (
                      'source_kind', 'source_food_id',
                      'nutrition_contract_version', 'source_snapshot'
                    ))
                    OR (table_name = 'meal_write_operation' AND column_name IN (
                      'user_id', 'operation', 'idempotency_key',
                      'request_fingerprint', 'status', 'lease_owner',
                      'lease_generation', 'lease_expires_at',
                      'target_meal_id', 'response'
                    ))
                  )
                """
            )
        )
        return _durable_write_schema_is_ready(result.all())


@dataclass(frozen=True)
class DurableWriteRecord:
    request_fingerprint: str
    response_status_code: int
    response_body: dict[str, Any]
    resource_id: str | None

    @property
    def is_pending(self) -> bool:
        return self.response_status_code == PENDING_RESPONSE_STATUS


def _to_record(row: DurableWriteRecordORM) -> DurableWriteRecord:
    return DurableWriteRecord(
        request_fingerprint=row.request_fingerprint,
        response_status_code=row.response_status_code,
        response_body=json.loads(row.response_body_json),
        resource_id=row.resource_id,
    )


def _apply_pending(
    row: DurableWriteRecordORM,
    *,
    request_fingerprint: str,
    now,
) -> None:
    row.request_fingerprint = request_fingerprint
    row.response_status_code = PENDING_RESPONSE_STATUS
    row.response_body_json = "{}"
    row.resource_id = None
    row.expires_at = now + timedelta(days=RETENTION_DAYS)
    row.created_at = now
    row.updated_at = now


async def get_durable_write(
    *,
    user_id: str,
    action: str,
    idempotency_key: str,
) -> DurableWriteRecord | None:
    async with AsyncUnitOfWork() as uow:
        row = await _fetch(uow, user_id, action, idempotency_key)
        if row is None or row.expires_at < utc_now():
            return None
        if row.response_status_code == PENDING_RESPONSE_STATUS:
            return None
        return _to_record(row)


async def begin_durable_write(
    *,
    user_id: str,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> DurableWriteRecord | None:
    """Claim the key before the mutation runs.

    Returns a completed record for exact replay, or None when this caller owns
    the claim and must proceed to create. Raises on fingerprint conflict or a
    fresh in-flight claim.
    """
    now = utc_now()
    try:
        async with AsyncUnitOfWork() as uow:
            existing = await _fetch(uow, user_id, action, idempotency_key)
            if existing is not None:
                return _begin_existing(
                    existing,
                    request_fingerprint=request_fingerprint,
                    now=now,
                )
            uow.session.add(
                DurableWriteRecordORM(
                    user_id=user_id,
                    action=action,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    response_status_code=PENDING_RESPONSE_STATUS,
                    response_body_json="{}",
                    resource_id=None,
                    expires_at=now + timedelta(days=RETENTION_DAYS),
                )
            )
            return None
    except IntegrityError as exc:
        return await _begin_after_race(
            user_id=user_id,
            action=action,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            exc=exc,
        )


def _begin_existing(
    existing: DurableWriteRecordORM,
    *,
    request_fingerprint: str,
    now,
) -> DurableWriteRecord | None:
    expired = existing.expires_at < now
    if existing.request_fingerprint != request_fingerprint:
        if expired:
            _apply_pending(existing, request_fingerprint=request_fingerprint, now=now)
            return None
        raise DurableWriteConflictError
    # Same fingerprint + pending means another request owns the mutation (or
    # completion failed after create). Never auto-reclaim — that would duplicate.
    if existing.response_status_code == PENDING_RESPONSE_STATUS:
        raise DurableWriteInProgressError
    if not expired:
        return _to_record(existing)
    _apply_pending(existing, request_fingerprint=request_fingerprint, now=now)
    return None


async def _begin_after_race(
    *,
    user_id: str,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
    exc: IntegrityError,
) -> DurableWriteRecord | None:
    async with AsyncUnitOfWork() as uow:
        raced = await _fetch(uow, user_id, action, idempotency_key)
        if raced is None:
            raise DurableWriteConflictError from exc
        return _begin_existing(
            raced,
            request_fingerprint=request_fingerprint,
            now=utc_now(),
        )


async def complete_durable_write(
    *,
    user_id: str,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
    response_status_code: int,
    response_body: dict[str, Any],
    resource_id: str | None = None,
) -> DurableWriteRecord:
    """Persist the exact response for a previously claimed key."""
    body_json = json.dumps(response_body, default=str, separators=(",", ":"))
    now = utc_now()
    async with AsyncUnitOfWork() as uow:
        existing = await _fetch(uow, user_id, action, idempotency_key)
        if existing is None:
            raise DurableWriteConflictError
        if existing.request_fingerprint != request_fingerprint:
            raise DurableWriteConflictError
        if (
            existing.response_status_code != PENDING_RESPONSE_STATUS
            and existing.expires_at >= now
        ):
            return _to_record(existing)
        existing.response_status_code = response_status_code
        existing.response_body_json = body_json
        existing.resource_id = resource_id
        existing.expires_at = now + timedelta(days=RETENTION_DAYS)
        return DurableWriteRecord(
            request_fingerprint=request_fingerprint,
            response_status_code=response_status_code,
            response_body=response_body,
            resource_id=resource_id,
        )


async def abandon_durable_write(
    *,
    user_id: str,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> None:
    """Drop a pending claim so a later retry can proceed."""
    async with AsyncUnitOfWork() as uow:
        existing = await _fetch(uow, user_id, action, idempotency_key)
        if existing is None:
            return
        if existing.request_fingerprint != request_fingerprint:
            return
        if existing.response_status_code != PENDING_RESPONSE_STATUS:
            return
        await uow.session.execute(
            delete(DurableWriteRecordORM).where(DurableWriteRecordORM.id == existing.id)
        )


async def _fetch(
    uow: AsyncUnitOfWork,
    user_id: str,
    action: str,
    key: str,
) -> DurableWriteRecordORM | None:
    result = await uow.session.execute(
        select(DurableWriteRecordORM).where(
            DurableWriteRecordORM.user_id == user_id,
            DurableWriteRecordORM.action == action,
            DurableWriteRecordORM.idempotency_key == key,
        )
    )
    return result.scalar_one_or_none()
