"""``entitlement_grants``: append-only, revoke-only manual grants (D5)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.billing.constants import ENTITLEMENT_STANDARD
from src.domain.model.billing.ledger import EntitlementGrant
from src.domain.ports.entitlement_ledger_repository_port import (
    EntitlementGrantRepositoryPort,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.billing import EntitlementGrantORM as Row


def _to_domain(row: Any) -> EntitlementGrant:
    return EntitlementGrant(
        id=row.id,
        user_id=row.user_id,
        entitlement_key=row.entitlement_key,
        source=row.source,
        source_ref=row.source_ref,
        reason=row.reason,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        revoked_at=row.revoked_at,
        revoke_reason=row.revoke_reason,
        revoked_by=row.revoked_by,
        created_by=row.created_by,
        rc_mirror_ref=row.rc_mirror_ref,
        created_at=row.created_at,
    )


class AsyncEntitlementGrantRepository(EntitlementGrantRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, grant: EntitlementGrant) -> EntitlementGrant:
        row = Row(
            id=grant.id,
            user_id=grant.user_id,
            entitlement_key=grant.entitlement_key,
            source=grant.source,
            source_ref=grant.source_ref,
            reason=grant.reason,
            starts_at=grant.starts_at,
            ends_at=grant.ends_at,
            revoked_at=grant.revoked_at,
            revoke_reason=grant.revoke_reason,
            revoked_by=grant.revoked_by,
            created_by=grant.created_by,
            rc_mirror_ref=grant.rc_mirror_ref,
            created_at=grant.created_at or utc_now(),
        )
        self.session.add(row)
        await self.session.flush()
        return _to_domain(row)

    async def get(self, grant_id: str) -> EntitlementGrant | None:
        row = await self.session.get(Row, grant_id, populate_existing=True)
        return _to_domain(row) if row else None

    async def find_by_source_ref(
        self,
        source: str,
        source_ref: str,
        entitlement_key: str = ENTITLEMENT_STANDARD,
    ) -> EntitlementGrant | None:
        row = (
            await self.session.execute(
                select(Row)
                .where(
                    Row.source == source,
                    Row.source_ref == source_ref,
                    Row.entitlement_key == entitlement_key,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_revoked: bool = True,
    ) -> list[EntitlementGrant]:
        statement = select(Row).where(Row.user_id == user_id)
        if entitlement_key is not None:
            statement = statement.where(Row.entitlement_key == entitlement_key)
        if not include_revoked:
            statement = statement.where(Row.revoked_at.is_(None))
        rows = await self.session.execute(
            statement.order_by(Row.starts_at, Row.created_at, Row.id).execution_options(
                populate_existing=True
            )
        )
        return [_to_domain(row) for row in rows.scalars()]

    async def revoke(
        self, grant_id: str, *, revoked_at: datetime, reason: str, revoked_by: str
    ) -> EntitlementGrant | None:
        row = (
            await self.session.execute(
                select(Row)
                .where(Row.id == grant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.revoked_at is None:
            row.revoked_at = revoked_at
            row.revoke_reason = reason
            row.revoked_by = revoked_by
            await self.session.flush()
        return _to_domain(row)
