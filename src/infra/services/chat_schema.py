"""Runtime check that Nutree-owned chat tables are migrated."""

from __future__ import annotations

from sqlalchemy import text

from src.infra.database.uow_async import AsyncUnitOfWork

_REQUIRED_CHAT_COLUMNS = {
    "chat_thread": {"id", "user_id"},
    "chat_message": {
        "id",
        "thread_id",
        "role",
        "status",
        "content",
        "idempotency_key",
        "citation_source_keys",
        "generation_lease_expires_at",
    },
}


def _chat_schema_is_ready(rows) -> bool:
    available: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        available.setdefault(table_name, set()).add(column_name)
    return all(
        required.issubset(available.get(table_name, set()))
        for table_name, required in _REQUIRED_CHAT_COLUMNS.items()
    )


async def chat_schema_is_ready() -> bool:
    """Return whether storage for the single-thread chat contract is available."""
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
                    (table_name = 'chat_thread' AND column_name IN (
                      'id', 'user_id'
                    ))
                    OR (table_name = 'chat_message' AND column_name IN (
                      'id', 'thread_id', 'role', 'status', 'content',
                      'idempotency_key', 'citation_source_keys',
                      'generation_lease_expires_at'
                    ))
                  )
                """
            )
        )
        return _chat_schema_is_ready(result.all())
