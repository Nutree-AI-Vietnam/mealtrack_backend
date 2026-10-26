"""Postgres-backed background AI job queue.

Admission follows the catalog preparation queue: one advisory lock serializes
the short claim decision across replicas, rows are leased with SKIP LOCKED,
and every worker write is fenced on the claim token and an unexpired lease.
All times come from ``clock_timestamp()`` so replicas share one clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, and_, case, func, null, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobNotFoundError,
    AiJobNotRetryableError,
)
from src.domain.model.ai_jobs import (
    ACTIVE_AI_JOB_STATUSES,
    NOTIFIABLE_AI_JOB_STATUSES,
    TERMINAL_AI_JOB_STATUSES,
    AiJob,
    AiJobClaim,
    AiJobError,
    AiJobErrorKind,
    AiJobHeartbeat,
    AiJobListState,
    AiJobNotificationClaim,
    AiJobStatus,
    AiJobType,
    NewAiJob,
)
from src.domain.ports.ai_job_repository_port import AiJobRepositoryPort
from src.infra.database.models.ai_job import AiJobORM as Job

# Distinct from the catalog preparation queue's key and outside the int32
# range that hashtext()-based locks use.
_ADMISSION_LOCK_KEY = 561_013_102_026_561

_ACTIVE = tuple(str(status) for status in ACTIVE_AI_JOB_STATUSES)
_TERMINAL = tuple(str(status) for status in TERMINAL_AI_JOB_STATUSES)
_NOTIFIABLE = tuple(str(status) for status in NOTIFIABLE_AI_JOB_STATUSES)
_QUEUED = str(AiJobStatus.QUEUED)
_RUNNING = str(AiJobStatus.RUNNING)
_COMPLETED = str(AiJobStatus.COMPLETED)
_FAILED = str(AiJobStatus.FAILED)
_CANCELLED = str(AiJobStatus.CANCELLED)

_INTERRUPTED = AiJobError(
    code="WORKER_INTERRUPTED", kind=AiJobErrorKind.TRANSIENT, retryable=True
)


def _clock():
    return func.clock_timestamp(type_=DateTime(timezone=True))


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


class AsyncAiJobRepository(AiJobRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Client-facing
    # ------------------------------------------------------------------

    async def schema_ready(self) -> bool:
        return bool(
            (
                await self.session.execute(
                    text(
                        "SELECT to_regclass('ai_jobs') IS NOT NULL AND EXISTS ("
                        " SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                        " AND table_name = 'user_fcm_tokens'"
                        " AND column_name = 'last_registered_at')"
                    )
                )
            ).scalar_one()
        )

    async def create(self, job: NewAiJob) -> tuple[AiJob, bool]:
        result = await self.session.execute(
            pg_insert(Job)
            .values(
                id=job.id,
                user_id=job.user_id,
                job_type=str(job.job_type),
                status=_QUEUED,
                language=job.language,
                request_payload=job.request_payload,
                request_fingerprint=job.request_fingerprint,
                dedupe_key=job.dedupe_key,
                max_attempts=job.max_attempts,
                available_at=_clock(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(Job.id)
        )
        created = result.scalar_one_or_none() is not None
        row = await self._load(job.id)
        if row is None:  # pragma: no cover - the insert or the conflict row exists
            raise AiJobNotFoundError(job.id)
        return _to_domain(row), created

    async def get(self, *, user_id: str, job_id: str) -> AiJob | None:
        row = await self._load(job_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_domain(row)

    async def list_by_ids(self, *, user_id: str, job_ids: Sequence[str]) -> list[AiJob]:
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return []
        rows = (
            await self.session.execute(
                select(Job)
                .where(Job.user_id == user_id, Job.id.in_(ids))
                .order_by(Job.created_at.desc(), Job.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [_to_domain(row) for row in rows]

    async def list_by_state(
        self,
        *,
        user_id: str,
        state: AiJobListState,
        since: datetime,
        limit: int,
    ) -> list[AiJob]:
        unseen = and_(Job.status.in_(_NOTIFIABLE), Job.seen_at.is_(None))
        active = Job.status.in_(_ACTIVE)
        condition = {
            AiJobListState.OPEN: or_(active, unseen),
            AiJobListState.ACTIVE: active,
            AiJobListState.UNSEEN: unseen,
        }[state]
        rows = (
            await self.session.execute(
                select(Job)
                .where(Job.user_id == user_id, Job.created_at >= since, condition)
                .order_by(Job.created_at.desc(), Job.id)
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [_to_domain(row) for row in rows]

    async def request_retry(self, *, user_id: str, job_id: str) -> AiJob:
        row = await self._lock_owned(user_id=user_id, job_id=job_id)
        if row.status != _FAILED or row.error_retryable is False:
            raise AiJobNotRetryableError(job_id, row.status)
        now = await self._now()
        row.status = _QUEUED
        # Exactly one more attempt: a manual retry never re-arms auto-retries.
        row.max_attempts = row.attempts + 1
        row.available_at = now
        _clear_outcome(row)
        await self.session.flush()
        return _to_domain(row)

    async def request_cancel(self, *, user_id: str, job_id: str) -> AiJob:
        row = await self._lock_owned(user_id=user_id, job_id=job_id)
        if row.status == _QUEUED:
            now = await self._now()
            row.status = _CANCELLED
            row.completed_at = now
            row.notify_after = None
            await self.session.flush()
        elif row.status == _RUNNING and row.cancel_requested_at is None:
            row.cancel_requested_at = await self._now()
            await self.session.flush()
        return _to_domain(row)

    async def mark_seen(self, *, user_id: str, job_ids: Sequence[str]) -> int:
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return 0
        result = await self.session.execute(
            update(Job)
            .where(
                Job.user_id == user_id,
                Job.id.in_(ids),
                Job.status.in_(_TERMINAL),
                Job.seen_at.is_(None),
            )
            .values(
                seen_at=_clock(),
                # Keep the due-notification index small; seen jobs never push.
                notify_after=case(
                    (Job.notified_at.is_(None), null()), else_=Job.notify_after
                ),
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def find_active_chat_job_id(
        self, *, user_id: str, dedupe_key: str
    ) -> str | None:
        return (
            await self.session.execute(
                select(Job.id)
                .where(
                    Job.user_id == user_id,
                    Job.job_type == str(AiJobType.CHAT),
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_(_ACTIVE),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def claim_next(
        self, *, lease_seconds: int, global_capacity: int
    ) -> AiJobClaim | None:
        if not 10 <= lease_seconds <= 3600 or not 1 <= global_capacity <= 64:
            raise ValueError("Invalid AI job lease/capacity")
        # All replicas serialize the short admission decision, then release
        # the transaction before any provider work starts.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMISSION_LOCK_KEY}
        )
        now = await self._now()
        expired = and_(Job.status == _RUNNING, Job.lease_expires_at <= now)
        # A worker died while the user was cancelling: honour the cancel.
        await self._execute_update(
            and_(expired, Job.cancel_requested_at.is_not(None)),
            status=_CANCELLED,
            claim_token=None,
            lease_expires_at=None,
            completed_at=now,
            notify_after=None,
        )
        # A worker died during the last allowed attempt: fail it visibly.
        await self._execute_update(
            and_(expired, Job.attempts >= Job.max_attempts),
            status=_FAILED,
            claim_token=None,
            lease_expires_at=None,
            completed_at=now,
            notify_after=now,
            **_error_values(_INTERRUPTED),
        )
        # Never strand a queued job that can no longer run.
        await self._execute_update(
            and_(Job.status == _QUEUED, Job.attempts >= Job.max_attempts),
            status=_FAILED,
            completed_at=now,
            notify_after=now,
            error_code=func.coalesce(Job.error_code, _INTERRUPTED.code),
            error_kind=func.coalesce(Job.error_kind, str(_INTERRUPTED.kind)),
            error_retryable=func.coalesce(Job.error_retryable, True),
        )
        # Chat turns streaming in the API process have their own concurrency
        # limits, so only non-chat work counts against worker capacity.
        running = (
            await self.session.execute(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.status == _RUNNING,
                    Job.lease_expires_at > now,
                    Job.job_type != str(AiJobType.CHAT),
                )
            )
        ).scalar_one()
        if running >= global_capacity:
            return None
        row = (
            await self.session.execute(
                select(Job)
                .where(
                    Job.attempts < Job.max_attempts,
                    Job.cancel_requested_at.is_(None),
                    or_(
                        and_(Job.status == _QUEUED, Job.available_at <= now),
                        expired,
                    ),
                )
                .order_by(Job.available_at, Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = _RUNNING
        row.claim_token = str(uuid4())
        row.lease_expires_at = now + _seconds(lease_seconds)
        row.attempts += 1
        row.started_at = row.started_at or now
        _set_error(row, None)
        await self.session.flush()
        return _to_claim(row)

    async def heartbeat(
        self, claim: AiJobClaim, *, lease_seconds: int
    ) -> AiJobHeartbeat:
        if not 10 <= lease_seconds <= 3600:
            raise ValueError("Invalid AI job lease")
        result = await self.session.execute(
            update(Job)
            .where(*_fence(claim))
            .values(lease_expires_at=_clock() + _seconds(lease_seconds))
            .returning(Job.cancel_requested_at)
            .execution_options(synchronize_session=False)
        )
        renewed = result.first()
        if renewed is None:
            return AiJobHeartbeat(renewed=False, cancel_requested=False)
        return AiJobHeartbeat(renewed=True, cancel_requested=renewed[0] is not None)

    async def complete(
        self,
        claim: AiJobClaim,
        *,
        result: dict[str, Any],
        notify_delay_seconds: int,
    ) -> bool:
        updated = await self.session.execute(
            update(Job)
            .where(*_fence(claim))
            .values(
                status=_COMPLETED,
                result_payload=result,
                claim_token=None,
                lease_expires_at=None,
                completed_at=_clock(),
                # Work that finished despite a cancel request stays completed
                # but is never announced.
                notify_after=case(
                    (
                        Job.cancel_requested_at.is_(None),
                        _clock() + _seconds(notify_delay_seconds),
                    ),
                    else_=None,
                ),
                **_error_values(None),
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return updated.first() is not None

    async def fail_attempt(
        self,
        claim: AiJobClaim,
        *,
        error: AiJobError,
        retry_delay_seconds: float | None,
        notify_delay_seconds: int,
    ) -> AiJobStatus | None:
        row = await self._lock_claimed(claim)
        if row is None:
            return None
        now = await self._now()
        row.claim_token = None
        row.lease_expires_at = None
        _set_error(row, error)
        if row.cancel_requested_at is not None:
            row.status = _CANCELLED
            row.completed_at = now
            row.notify_after = None
        elif retry_delay_seconds is not None:
            row.status = _QUEUED
            row.available_at = now + _seconds(retry_delay_seconds)
        else:
            row.status = _FAILED
            row.completed_at = now
            row.notify_after = now + _seconds(notify_delay_seconds)
        await self.session.flush()
        return AiJobStatus(row.status)

    async def cancel_claimed(self, claim: AiJobClaim) -> bool:
        updated = await self.session.execute(
            update(Job)
            .where(*_fence(claim))
            .values(
                status=_CANCELLED,
                claim_token=None,
                lease_expires_at=None,
                completed_at=_clock(),
                notify_after=None,
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return updated.first() is not None

    async def start_chat_job(
        self, job: NewAiJob, *, lease_seconds: int
    ) -> AiJobClaim | None:
        if not 10 <= lease_seconds <= 3600:
            raise ValueError("Invalid AI job lease")
        now = await self._now()
        lease_expires_at = now + _seconds(lease_seconds)
        claim_token = str(uuid4())
        inserted = await self.session.execute(
            pg_insert(Job)
            .values(
                id=job.id,
                user_id=job.user_id,
                job_type=str(AiJobType.CHAT),
                status=_RUNNING,
                language=job.language,
                request_payload=job.request_payload,
                request_fingerprint=job.request_fingerprint,
                dedupe_key=job.dedupe_key,
                attempts=1,
                max_attempts=job.max_attempts,
                available_at=now,
                claim_token=claim_token,
                lease_expires_at=lease_expires_at,
                started_at=now,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(Job.id)
        )
        if inserted.scalar_one_or_none() is not None:
            return AiJobClaim(
                id=job.id,
                user_id=job.user_id,
                job_type=AiJobType.CHAT,
                language=job.language,
                request_payload=job.request_payload,
                claim_token=claim_token,
                attempts=1,
                max_attempts=job.max_attempts,
                lease_expires_at=lease_expires_at,
            )
        row = await self._lock(job.id)
        if row is None:  # pragma: no cover - the conflicting row exists
            raise AiJobNotFoundError(job.id)
        if (
            row.user_id != job.user_id
            or row.job_type != str(AiJobType.CHAT)
            or row.request_fingerprint != job.request_fingerprint
        ):
            raise AiJobConflictError(job.id)
        lease_lost = (
            row.status == _RUNNING
            and row.lease_expires_at is not None
            and row.lease_expires_at <= now
        )
        if row.status not in (_QUEUED, _FAILED) and not lease_lost:
            return None
        # The user re-sent the turn: take the job over in this process.
        row.status = _RUNNING
        row.attempts += 1
        row.max_attempts = max(row.max_attempts, row.attempts)
        row.claim_token = claim_token
        row.lease_expires_at = lease_expires_at
        row.started_at = row.started_at or now
        _clear_outcome(row, keep_claim=True)
        await self.session.flush()
        return _to_claim(row)

    # ------------------------------------------------------------------
    # Notifier
    # ------------------------------------------------------------------

    async def claim_notifications(
        self, *, limit: int, max_attempts: int, stale_after_seconds: int
    ) -> list[AiJobNotificationClaim]:
        if not 1 <= limit <= 100 or max_attempts < 1:
            raise ValueError("Invalid AI job notification batch")
        now = await self._now()
        pending = and_(Job.notified_at.is_(None), Job.notify_after.is_not(None))
        # Drop pushes that would arrive too late, that the user has already
        # made redundant, or that keep failing.
        await self._execute_update(
            and_(
                pending,
                or_(
                    Job.notify_after <= now - _seconds(stale_after_seconds),
                    Job.seen_at.is_not(None),
                    Job.notify_attempts >= max_attempts,
                    Job.status.not_in(_NOTIFIABLE),
                ),
            ),
            notify_after=None,
        )
        rows = (
            (
                await self.session.execute(
                    select(Job)
                    .where(pending, Job.notify_after <= now)
                    .order_by(Job.notify_after, Job.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        claims: list[AiJobNotificationClaim] = []
        for row in rows:
            # Marked before sending: a crash may lose a push, never repeat it.
            row.notified_at = now
            row.notify_attempts += 1
            claims.append(
                AiJobNotificationClaim(
                    job_id=row.id,
                    user_id=row.user_id,
                    job_type=AiJobType(row.job_type),
                    status=AiJobStatus(row.status),
                    language=row.language,
                    result=row.result_payload,
                    error_code=row.error_code,
                    error_retryable=row.error_retryable,
                    notify_attempts=row.notify_attempts,
                )
            )
        if claims:
            await self.session.flush()
        return claims

    async def release_notification(
        self, *, job_id: str, retry_delay_seconds: float
    ) -> None:
        await self._execute_update(
            and_(
                Job.id == job_id,
                Job.notified_at.is_not(None),
                Job.seen_at.is_(None),
            ),
            notified_at=None,
            notify_after=_clock() + _seconds(retry_delay_seconds),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _now(self) -> datetime:
        return (await self.session.execute(select(_clock()))).scalar_one()

    async def _load(self, job_id: str) -> Job | None:
        return (
            await self.session.execute(
                select(Job)
                .where(Job.id == job_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _lock(self, job_id: str) -> Job | None:
        return (
            await self.session.execute(
                select(Job)
                .where(Job.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _lock_owned(self, *, user_id: str, job_id: str) -> Job:
        row = await self._lock(job_id)
        if row is None or row.user_id != user_id:
            raise AiJobNotFoundError(job_id)
        return row

    async def _lock_claimed(self, claim: AiJobClaim) -> Job | None:
        return (
            await self.session.execute(
                select(Job)
                .where(*_fence(claim))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _execute_update(self, condition, **values) -> int:
        result = await self.session.execute(
            update(Job)
            .where(condition)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)


def _fence(claim: AiJobClaim):
    return (
        Job.id == claim.id,
        Job.status == _RUNNING,
        Job.claim_token == claim.claim_token,
        Job.lease_expires_at > _clock(),
    )


def _error_values(error: AiJobError | None) -> dict[str, Any]:
    if error is None:
        return {"error_code": None, "error_kind": None, "error_retryable": None}
    return {
        "error_code": error.code,
        "error_kind": str(error.kind),
        "error_retryable": error.retryable,
    }


def _set_error(row: Job, error: AiJobError | None) -> None:
    for name, value in _error_values(error).items():
        setattr(row, name, value)


def _clear_outcome(row: Job, *, keep_claim: bool = False) -> None:
    """Reset everything a previous attempt left behind."""
    _set_error(row, None)
    row.result_payload = None
    row.completed_at = None
    row.seen_at = None
    row.cancel_requested_at = None
    row.notify_after = None
    row.notified_at = None
    row.notify_attempts = 0
    if not keep_claim:
        row.claim_token = None
        row.lease_expires_at = None


def _to_error(row: Job) -> AiJobError | None:
    if row.error_code is None:
        return None
    return AiJobError(
        code=row.error_code,
        kind=AiJobErrorKind(row.error_kind or AiJobErrorKind.TRANSIENT),
        retryable=row.error_retryable is not False,
    )


def _to_domain(row: Job) -> AiJob:
    return AiJob(
        id=row.id,
        user_id=row.user_id,
        job_type=AiJobType(row.job_type),
        status=AiJobStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        language=row.language,
        request_payload=dict(row.request_payload or {}),
        request_fingerprint=row.request_fingerprint,
        available_at=row.available_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        error=_to_error(row),
        result=dict(row.result_payload) if row.result_payload is not None else None,
        started_at=row.started_at,
        completed_at=row.completed_at,
        seen_at=row.seen_at,
        cancel_requested_at=row.cancel_requested_at,
        notified_at=row.notified_at,
    )


def _to_claim(row: Job) -> AiJobClaim:
    return AiJobClaim(
        id=row.id,
        user_id=row.user_id,
        job_type=AiJobType(row.job_type),
        language=row.language,
        request_payload=dict(row.request_payload or {}),
        claim_token=row.claim_token,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        lease_expires_at=row.lease_expires_at,
    )
