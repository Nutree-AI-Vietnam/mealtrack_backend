"""Async SQLAlchemy repository for Nutree-owned chat persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
)
from src.domain.model.chat import (
    ChatClaimKind,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatThread,
    ChatTurnClaim,
    ChatUsage,
)
from src.domain.ports.chat_repository_port import ChatRepositoryPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.chat import ChatMessageORM, ChatThreadORM


class AsyncChatRepository(ChatRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_thread(self, user_id: str) -> ChatThread:
        now = utc_now()
        thread_id = str(uuid.uuid4())
        stmt = (
            insert(ChatThreadORM)
            .values(
                id=thread_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        await self.session.execute(stmt)
        thread = await self._get_thread_row(user_id)
        if thread is None:
            raise RuntimeError("chat thread insert did not persist")
        return _to_thread(thread)

    async def get_thread(self, user_id: str) -> ChatThread | None:
        row = await self._get_thread_row(user_id)
        return _to_thread(row) if row else None

    async def claim_turn(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        request_fingerprint: str,
        lease_expires_at: datetime,
    ) -> ChatTurnClaim:
        thread = await self.get_or_create_thread(user_id)
        now = utc_now()

        existing_user = await self._user_message_for_key(thread.id, idempotency_key)
        if existing_user is not None:
            return await self._claim_existing(
                thread,
                existing_user,
                request_fingerprint=request_fingerprint,
                lease_expires_at=lease_expires_at,
                now=now,
            )

        await self._reclaim_expired_generation(thread.id, now)

        active = await self._active_generation(thread.id)
        if active is not None:
            raise ChatBusyError()

        user_row = ChatMessageORM(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role=ChatMessageRole.USER.value,
            status=ChatMessageStatus.COMPLETED.value,
            content=content,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            citation_source_keys=[],
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        assistant_row = ChatMessageORM(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role=ChatMessageRole.ASSISTANT.value,
            status=ChatMessageStatus.GENERATING.value,
            in_reply_to_id=user_row.id,
            citation_source_keys=[],
            generation_lease_expires_at=lease_expires_at,
            created_at=now,
            updated_at=now,
        )
        self.session.add(user_row)
        self.session.add(assistant_row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raced = await self._user_message_for_key(thread.id, idempotency_key)
            if raced is not None:
                thread = await self.get_or_create_thread(user_id)
                return await self._claim_existing(
                    thread,
                    raced,
                    request_fingerprint=request_fingerprint,
                    lease_expires_at=lease_expires_at,
                    now=utc_now(),
                )
            raise ChatBusyError() from exc

        await self._touch_thread(thread.id, now)
        return ChatTurnClaim(
            kind=ChatClaimKind.NEW,
            thread=thread,
            user_message=_to_message(user_row),
            assistant_message=_to_message(assistant_row),
        )

    async def list_completed_messages(
        self,
        *,
        thread_id: str,
        limit: int,
        before_message_id: str | None = None,
    ) -> list[ChatMessage]:
        stmt = (
            select(ChatMessageORM)
            .where(
                ChatMessageORM.thread_id == thread_id,
                ChatMessageORM.status == ChatMessageStatus.COMPLETED.value,
            )
            .order_by(ChatMessageORM.created_at.desc(), ChatMessageORM.id.desc())
            .limit(limit)
        )
        if before_message_id:
            cursor = await self.session.get(ChatMessageORM, before_message_id)
            if cursor is not None:
                stmt = stmt.where(
                    (ChatMessageORM.created_at < cursor.created_at)
                    | (
                        (ChatMessageORM.created_at == cursor.created_at)
                        & (ChatMessageORM.id < cursor.id)
                    )
                )
        result = await self.session.execute(stmt)
        return [_to_message(row) for row in result.scalars().all()]

    async def list_recent_completed_history(
        self,
        *,
        thread_id: str,
        limit: int,
    ) -> list[ChatMessage]:
        recent = await self.list_completed_messages(thread_id=thread_id, limit=limit)
        return list(reversed(recent))

    async def complete_assistant_message(
        self,
        *,
        message_id: str,
        content: str,
        model: str,
        usage: ChatUsage,
        prompt_version: str,
        context_version: str,
        citation_source_keys: tuple[str, ...],
        provider_response_id: str | None,
    ) -> ChatMessage:
        now = utc_now()
        await self.session.execute(
            update(ChatMessageORM)
            .where(
                ChatMessageORM.id == message_id,
                ChatMessageORM.role == ChatMessageRole.ASSISTANT.value,
            )
            .values(
                status=ChatMessageStatus.COMPLETED.value,
                content=content,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                prompt_version=prompt_version,
                context_version=context_version,
                citation_source_keys=list(citation_source_keys),
                provider_response_id=provider_response_id,
                generation_lease_expires_at=None,
                error_code=None,
                completed_at=now,
                updated_at=now,
            )
        )
        row = await self.session.get(ChatMessageORM, message_id)
        if row is None:
            raise RuntimeError("assistant message missing after completion")
        await self._touch_thread(row.thread_id, now)
        return _to_message(row)

    async def fail_assistant_message(
        self,
        *,
        message_id: str,
        error_code: str,
        content: str | None = None,
    ) -> ChatMessage | None:
        now = utc_now()
        values: dict[str, object] = {
            "status": ChatMessageStatus.FAILED.value,
            "error_code": error_code,
            "generation_lease_expires_at": None,
            "updated_at": now,
        }
        if content is not None:
            values["content"] = content
        await self.session.execute(
            update(ChatMessageORM)
            .where(ChatMessageORM.id == message_id)
            .values(**values)
        )
        row = await self.session.get(ChatMessageORM, message_id)
        return _to_message(row) if row else None

    async def count_user_turns_since(self, *, user_id: str, since: datetime) -> int:
        thread = await self._get_thread_row(user_id)
        if thread is None:
            return 0
        result = await self.session.execute(
            select(func.count())
            .select_from(ChatMessageORM)
            .where(
                ChatMessageORM.thread_id == thread.id,
                ChatMessageORM.role == ChatMessageRole.USER.value,
                ChatMessageORM.created_at >= since,
            )
        )
        return int(result.scalar_one())

    async def clear_thread(self, user_id: str) -> ChatThread:
        thread = await self.get_or_create_thread(user_id)
        now = utc_now()
        await self.session.execute(
            update(ChatThreadORM)
            .where(ChatThreadORM.id == thread.id)
            .values(summary=None, summary_through_message_id=None, updated_at=now)
        )
        await self.session.execute(
            delete(ChatMessageORM).where(ChatMessageORM.thread_id == thread.id)
        )
        refreshed = await self.session.get(ChatThreadORM, thread.id)
        if refreshed is None:
            raise RuntimeError("chat thread missing after clear")
        return _to_thread(refreshed)

    async def delete_user_chat(self, user_id: str) -> None:
        thread = await self._get_thread_row(user_id)
        if thread is None:
            return
        await self.session.execute(
            update(ChatThreadORM)
            .where(ChatThreadORM.id == thread.id)
            .values(summary=None, summary_through_message_id=None)
        )
        await self.session.execute(
            delete(ChatMessageORM).where(ChatMessageORM.thread_id == thread.id)
        )
        await self.session.execute(
            delete(ChatThreadORM).where(ChatThreadORM.id == thread.id)
        )

    async def _claim_existing(
        self,
        thread: ChatThread,
        user_row: ChatMessageORM,
        *,
        request_fingerprint: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> ChatTurnClaim:
        if user_row.request_fingerprint != request_fingerprint:
            raise ChatIdempotencyConflictError()

        assistant = await self._reply_for(user_row.id)
        if assistant is not None:
            if assistant.status == ChatMessageStatus.COMPLETED.value:
                return ChatTurnClaim(
                    kind=ChatClaimKind.REPLAY,
                    thread=thread,
                    user_message=_to_message(user_row),
                    assistant_message=_to_message(assistant),
                )
            if assistant.status == ChatMessageStatus.GENERATING.value:
                expires = assistant.generation_lease_expires_at
                if expires is not None and expires > now:
                    raise ChatBusyError()
                await self.fail_assistant_message(
                    message_id=assistant.id,
                    error_code="lease_expired",
                )

        await self._reclaim_expired_generation(thread.id, now)
        active = await self._active_generation(thread.id)
        if active is not None:
            raise ChatBusyError()

        assistant_row = ChatMessageORM(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            role=ChatMessageRole.ASSISTANT.value,
            status=ChatMessageStatus.GENERATING.value,
            in_reply_to_id=user_row.id,
            citation_source_keys=[],
            generation_lease_expires_at=lease_expires_at,
            created_at=now,
            updated_at=now,
        )
        self.session.add(assistant_row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ChatBusyError() from exc
        await self._touch_thread(thread.id, now)
        return ChatTurnClaim(
            kind=ChatClaimKind.NEW,
            thread=thread,
            user_message=_to_message(user_row),
            assistant_message=_to_message(assistant_row),
        )

    async def _get_thread_row(self, user_id: str) -> ChatThreadORM | None:
        result = await self.session.execute(
            select(ChatThreadORM).where(ChatThreadORM.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _user_message_for_key(
        self, thread_id: str, idempotency_key: str
    ) -> ChatMessageORM | None:
        result = await self.session.execute(
            select(ChatMessageORM).where(
                ChatMessageORM.thread_id == thread_id,
                ChatMessageORM.role == ChatMessageRole.USER.value,
                ChatMessageORM.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def _reply_for(self, user_message_id: str) -> ChatMessageORM | None:
        result = await self.session.execute(
            select(ChatMessageORM)
            .where(ChatMessageORM.in_reply_to_id == user_message_id)
            .order_by(ChatMessageORM.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _active_generation(self, thread_id: str) -> ChatMessageORM | None:
        result = await self.session.execute(
            select(ChatMessageORM).where(
                ChatMessageORM.thread_id == thread_id,
                ChatMessageORM.role == ChatMessageRole.ASSISTANT.value,
                ChatMessageORM.status == ChatMessageStatus.GENERATING.value,
            )
        )
        return result.scalar_one_or_none()

    async def _reclaim_expired_generation(self, thread_id: str, now: datetime) -> None:
        await self.session.execute(
            update(ChatMessageORM)
            .where(
                ChatMessageORM.thread_id == thread_id,
                ChatMessageORM.role == ChatMessageRole.ASSISTANT.value,
                ChatMessageORM.status == ChatMessageStatus.GENERATING.value,
                ChatMessageORM.generation_lease_expires_at.is_not(None),
                ChatMessageORM.generation_lease_expires_at <= now,
            )
            .values(
                status=ChatMessageStatus.FAILED.value,
                error_code="lease_expired",
                generation_lease_expires_at=None,
                updated_at=now,
            )
        )

    async def _touch_thread(self, thread_id: str, now: datetime) -> None:
        await self.session.execute(
            update(ChatThreadORM)
            .where(ChatThreadORM.id == thread_id)
            .values(updated_at=now)
        )


def _to_thread(row: ChatThreadORM) -> ChatThread:
    return ChatThread(
        id=row.id,
        user_id=row.user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        summary=row.summary,
        summary_through_message_id=row.summary_through_message_id,
    )


def _to_message(row: ChatMessageORM) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        thread_id=row.thread_id,
        role=ChatMessageRole(row.role),
        status=ChatMessageStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        content=row.content,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        in_reply_to_id=row.in_reply_to_id,
        model=row.model,
        provider_response_id=row.provider_response_id,
        prompt_version=row.prompt_version,
        context_version=row.context_version,
        citation_source_keys=tuple(row.citation_source_keys or ()),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cached_tokens=row.cached_tokens,
        generation_lease_expires_at=row.generation_lease_expires_at,
        error_code=row.error_code,
        completed_at=row.completed_at,
    )
