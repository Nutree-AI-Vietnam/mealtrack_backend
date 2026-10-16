"""``billing_customer_identities``: provider customer ids per user."""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.billing.constants import (
    DEFAULT_PROJECT,
    PROVIDER_REVENUECAT,
    IdentityKind,
)
from src.domain.model.billing.ledger import BillingCustomerIdentity
from src.domain.ports.entitlement_ledger_repository_port import (
    BillingCustomerIdentityRepositoryPort,
    IdentityLinkOutcome,
)
from src.infra.database.models.billing import BillingCustomerIdentityORM as Row

logger = logging.getLogger(__name__)


def _to_domain(row: Any) -> BillingCustomerIdentity:
    return BillingCustomerIdentity(
        id=row.id,
        user_id=row.user_id,
        provider=row.provider,
        project=row.project,
        provider_customer_id=row.provider_customer_id,
        kind=row.kind,
        retired_at=row.retired_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AsyncBillingCustomerIdentityRepository(BillingCustomerIdentityRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_primary(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        project: str = DEFAULT_PROJECT,
    ) -> BillingCustomerIdentity | None:
        row = (
            await self.session.execute(
                select(Row).where(
                    Row.user_id == user_id,
                    Row.provider == provider,
                    Row.project == project,
                    Row.kind == IdentityKind.PRIMARY,
                    Row.retired_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list_for_user(
        self,
        user_id: str,
        provider: str = PROVIDER_REVENUECAT,
        *,
        include_retired: bool = False,
    ) -> list[BillingCustomerIdentity]:
        statement = select(Row).where(Row.user_id == user_id, Row.provider == provider)
        if not include_retired:
            statement = statement.where(Row.retired_at.is_(None))
        primary_first = case((Row.kind == IdentityKind.PRIMARY, 0), else_=1)
        rows = (
            await self.session.execute(
                statement.order_by(primary_first, Row.first_seen_at, Row.id)
            )
        ).scalars()
        return [_to_domain(row) for row in rows]

    async def find_by_customer_id(
        self,
        provider: str,
        provider_customer_id: str,
        project: str = DEFAULT_PROJECT,
        *,
        include_retired: bool = False,
    ) -> BillingCustomerIdentity | None:
        row = await self._row_for_customer(provider, project, provider_customer_id)
        if row is None or (row.retired_at is not None and not include_retired):
            return None
        return _to_domain(row)

    async def upsert_primary(
        self,
        user_id: str,
        provider: str,
        provider_customer_id: str,
        *,
        seen_at: datetime,
        project: str = DEFAULT_PROJECT,
    ) -> IdentityLinkOutcome:
        existing = await self._row_for_customer(
            provider, project, provider_customer_id, for_update=True
        )
        if existing is not None and existing.retired_at is None:
            if existing.user_id != user_id:
                if existing.kind == IdentityKind.PRIMARY:
                    return IdentityLinkOutcome.CONFLICT
                # Another user holds this user's own Firebase UID as an alias or
                # legacy lead (e.g. seeded from a TRANSFER-rewritten legacy row).
                # Primary beats every other kind: take it back.
                logger.warning(
                    "Primary billing identity reclaimed from another user",
                    extra={
                        "user_id": user_id,
                        "from_user_id": existing.user_id,
                        "kind": existing.kind,
                        "provider": provider,
                    },
                )
                await self._demote_primary(user_id, provider, project)
                existing.user_id = user_id
                existing.kind = IdentityKind.PRIMARY
                existing.first_seen_at = seen_at
                existing.last_seen_at = seen_at
                existing.updated_at = seen_at
                await self.session.flush()
                return IdentityLinkOutcome.RECLAIMED
            existing.last_seen_at = max(existing.last_seen_at, seen_at)
            if existing.kind != IdentityKind.PRIMARY:
                await self._demote_primary(user_id, provider, project)
                existing.kind = IdentityKind.PRIMARY
            await self.session.flush()
            return IdentityLinkOutcome.ALREADY_LINKED

        if existing is not None and existing.user_id == user_id:
            # Never un-retire a deleted account's own row (D11).
            return IdentityLinkOutcome.RETIRED
        await self._demote_primary(user_id, provider, project)
        if existing is not None:
            # A retired row (deleted account, D11) keeps the unique customer id;
            # a re-registered account with the same Firebase UID reclaims it.
            existing.user_id = user_id
            existing.kind = IdentityKind.PRIMARY
            existing.retired_at = None
            existing.first_seen_at = seen_at
            existing.last_seen_at = seen_at
            await self.session.flush()
            return IdentityLinkOutcome.INSERTED

        inserted = await self._insert(
            user_id,
            provider,
            project,
            provider_customer_id,
            IdentityKind.PRIMARY,
            seen_at=seen_at,
        )
        if inserted:
            return IdentityLinkOutcome.INSERTED
        return await self._outcome_after_race(
            user_id, provider, project, provider_customer_id, seen_at
        )

    async def add_alias(
        self,
        user_id: str,
        provider: str,
        provider_customer_id: str,
        kind: str,
        *,
        seen_at: datetime,
        project: str = DEFAULT_PROJECT,
    ) -> IdentityLinkOutcome:
        if kind == IdentityKind.PRIMARY or kind not in set(IdentityKind):
            raise ValueError(f"invalid alias kind: {kind}")
        try:
            # SAVEPOINT: an alias write can never abort the caller's sync.
            async with self.session.begin_nested():
                inserted = await self._insert(
                    user_id,
                    provider,
                    project,
                    provider_customer_id,
                    kind,
                    seen_at=seen_at,
                )
        except SQLAlchemyError:
            logger.warning(
                "Billing alias insert failed; continuing",
                extra={"provider": provider, "user_id": user_id},
                exc_info=True,
            )
            return IdentityLinkOutcome.CONFLICT
        if inserted:
            return IdentityLinkOutcome.INSERTED
        return await self._outcome_after_race(
            user_id, provider, project, provider_customer_id, seen_at
        )

    async def retire_for_user(self, user_id: str, *, retired_at: datetime) -> int:
        result = await self.session.execute(
            update(Row)
            .where(Row.user_id == user_id, Row.retired_at.is_(None))
            .values(retired_at=retired_at, updated_at=retired_at)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def _insert(
        self,
        user_id: str,
        provider: str,
        project: str,
        provider_customer_id: str,
        kind: str,
        *,
        seen_at: datetime,
    ) -> bool:
        statement = (
            pg_insert(Row)
            .values(
                id=str(uuid.uuid4()),
                user_id=user_id,
                provider=provider,
                project=project,
                provider_customer_id=provider_customer_id,
                kind=kind,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                created_at=seen_at,
                updated_at=seen_at,
            )
            # No conflict target: any unique violation (customer id or the
            # one-live-primary index) is skipped, never raised.
            .on_conflict_do_nothing()
            .returning(Row.id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def _outcome_after_race(
        self,
        user_id: str,
        provider: str,
        project: str,
        provider_customer_id: str,
        seen_at: datetime,
    ) -> IdentityLinkOutcome:
        existing = await self._row_for_customer(provider, project, provider_customer_id)
        if existing is None or existing.user_id != user_id:
            return IdentityLinkOutcome.CONFLICT
        await self.session.execute(
            update(Row)
            .where(Row.id == existing.id, Row.last_seen_at < seen_at)
            .values(last_seen_at=seen_at, updated_at=seen_at)
        )
        return IdentityLinkOutcome.ALREADY_LINKED

    async def _demote_primary(self, user_id: str, provider: str, project: str) -> None:
        """At most one live primary per user: an older one becomes an alias."""
        await self.session.execute(
            update(Row)
            .where(
                Row.user_id == user_id,
                Row.provider == provider,
                Row.project == project,
                Row.kind == IdentityKind.PRIMARY,
                Row.retired_at.is_(None),
            )
            .values(kind=IdentityKind.ALIAS)
        )

    async def _row_for_customer(
        self,
        provider: str,
        project: str,
        provider_customer_id: str,
        *,
        for_update: bool = False,
    ) -> Any:
        statement = select(Row).where(
            Row.provider == provider,
            Row.project == project,
            Row.provider_customer_id == provider_customer_id,
        )
        if for_update:
            statement = statement.with_for_update()
        statement = statement.execution_options(populate_existing=True)
        return (await self.session.execute(statement)).scalar_one_or_none()
