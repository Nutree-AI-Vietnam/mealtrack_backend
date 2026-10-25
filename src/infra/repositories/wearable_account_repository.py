"""Stores the Open Wearables user id on the Nutree user row."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.database.models.user.user import User


async def load_open_wearables_user_id(
    session: AsyncSession,
    user_id: str,
) -> str | None:
    result = await session.execute(
        select(User.open_wearables_user_id).where(User.id == user_id)
    )
    value = result.scalar_one_or_none()
    return value if isinstance(value, str) and value else None


async def save_open_wearables_user_id(
    session: AsyncSession,
    user_id: str,
    open_wearables_user_id: str,
) -> str:
    """Persist the id once. A concurrent create keeps the first stored UUID.

    The caller's unit of work commits. The guarded UPDATE waits on a
    concurrent writer's row lock, so a lost race re-reads the winner's id.
    """
    existing = await load_open_wearables_user_id(session, user_id)
    if existing:
        return existing
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.open_wearables_user_id.is_(None))
        .values(open_wearables_user_id=open_wearables_user_id)
    )
    if result.rowcount == 1:
        return open_wearables_user_id
    stored = await load_open_wearables_user_id(session, user_id)
    return stored or open_wearables_user_id
