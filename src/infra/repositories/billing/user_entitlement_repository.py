"""``user_entitlements``: the projection, keyed by (user_id, entitlement_key)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.billing.constants import ENTITLEMENT_STANDARD
from src.domain.model.billing.ledger import UserEntitlement
from src.domain.ports.entitlement_ledger_repository_port import (
    UserEntitlementRepositoryPort,
)
from src.infra.database.models.billing import UserEntitlementORM as Row
from src.infra.database.models.user.user import User

_COLUMNS = (
    "user_id",
    "entitlement_key",
    "has_access",
    "state",
    "access_until",
    "is_lifetime",
    "next_change_at",
    "will_renew",
    "period_type",
    "primary_source",
    "primary_ref",
    "product_id",
    "store",
    "is_sandbox",
    "ever_had_access",
    "version",
    "computed_at",
    "last_provider_sync_at",
    "last_purchase_hint_at",
)
_KEY_COLUMNS = ("user_id", "entitlement_key")
# user_id is a String(36) uuid, so the advisory key goes through hashtext()
# like the existing webhook TRANSFER lock.
_LOCK_USER = text("SELECT pg_advisory_xact_lock(hashtext('ent:' || :user_id))")


def _to_domain(row: Any) -> UserEntitlement:
    return UserEntitlement(**{column: getattr(row, column) for column in _COLUMNS})


class AsyncUserEntitlementRepository(UserEntitlementRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_users(self, user_ids: Sequence[str]) -> None:
        for user_id in sorted(set(user_ids)):
            await self.session.execute(_LOCK_USER, {"user_id": user_id})

    async def get(
        self, user_id: str, entitlement_key: str = ENTITLEMENT_STANDARD
    ) -> UserEntitlement | None:
        row = await self.session.get(
            Row, (user_id, entitlement_key), populate_existing=True
        )
        return _to_domain(row) if row else None

    async def list_for_user(self, user_id: str) -> list[UserEntitlement]:
        rows = await self.session.execute(
            select(Row)
            .where(Row.user_id == user_id)
            .order_by(Row.entitlement_key)
            .execution_options(populate_existing=True)
        )
        return [_to_domain(row) for row in rows.scalars()]

    async def upsert(self, projection: UserEntitlement) -> None:
        values = {column: getattr(projection, column) for column in _COLUMNS}
        insert = pg_insert(Row).values(values)
        await self.session.execute(
            insert.on_conflict_do_update(
                index_elements=list(_KEY_COLUMNS),
                set_={
                    column: insert.excluded[column]
                    for column in _COLUMNS
                    if column not in _KEY_COLUMNS
                },
            )
        )

    async def set_last_purchase_hint_at(
        self, user_id: str, entitlement_key: str, *, at: datetime
    ) -> None:
        await self.session.execute(
            update(Row)
            .where(Row.user_id == user_id, Row.entitlement_key == entitlement_key)
            .values(last_purchase_hint_at=at)
            .execution_options(synchronize_session=False)
        )

    async def list_due_for_recompute(
        self, *, now: datetime, limit: int
    ) -> list[tuple[str, str]]:
        rows = await self.session.execute(
            select(Row.user_id, Row.entitlement_key)
            .where(Row.next_change_at <= now)
            .order_by(Row.next_change_at, Row.user_id)
            .limit(limit)
        )
        return [(row.user_id, row.entitlement_key) for row in rows]

    async def list_user_ids_for_resync(
        self, *, synced_before: datetime, limit: int
    ) -> list[str]:
        rows = await self.session.execute(
            select(Row.user_id)
            .join(User, User.id == Row.user_id)
            .where(
                User.is_active.is_(True),
                Row.has_access.is_(True),
                or_(
                    Row.last_provider_sync_at.is_(None),
                    Row.last_provider_sync_at < synced_before,
                ),
            )
            .group_by(Row.user_id)
            .order_by(func.min(Row.last_provider_sync_at).asc().nulls_first())
            .limit(limit)
        )
        return [row.user_id for row in rows]
