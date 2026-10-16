"""``store_subscriptions``: one row per provider subscription key."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from src.domain.model.billing.constants import StoreSubscriptionState
from src.domain.model.billing.ledger import StoreSubscription
from src.domain.ports.entitlement_ledger_repository_port import (
    StoreSubscriptionRepositoryPort,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.billing import StoreSubscriptionORM as Row

_COLUMNS = (
    "id",
    "user_id",
    "provider",
    "store",
    "environment",
    "subscription_key",
    "source_customer_id",
    "store_identifier",
    "original_transaction_id",
    "product_id",
    "entitlement_key",
    "ownership_type",
    "period_type",
    "state",
    "will_renew",
    "is_lifetime",
    "purchased_at",
    "expires_at",
    "grace_expires_at",
    "unsubscribe_detected_at",
    "billing_issue_detected_at",
    "refunded_at",
    "absent_since",
    "fetched_at",
    "last_synced_at",
    "version",
    "created_at",
    "updated_at",
)
# Two-key advisory locks live in a key space separate from the one-key
# ``hashtext('ent:'||user_id)`` user locks; 680 tags this ledger (NM-680).
_KEY_LOCK_SPACE = 680
_KEY_HASHES = text(
    "SELECT DISTINCT hashtext(k) AS h FROM unnest(:keys) AS k ORDER BY h"
).bindparams(bindparam("keys", type_=ARRAY(String())))
_LOCK_KEY = text("SELECT pg_advisory_xact_lock(:space, :hash)")
# On a key conflict the existing row keeps its id and created_at.
_UPDATE_COLUMNS = tuple(c for c in _COLUMNS if c not in ("id", "created_at"))


def _to_domain(row: Any) -> StoreSubscription:
    return StoreSubscription(**{column: getattr(row, column) for column in _COLUMNS})


def _values(sub: StoreSubscription) -> dict[str, Any]:
    values = {column: getattr(sub, column) for column in _COLUMNS}
    now = utc_now()
    values["created_at"] = values["created_at"] or now
    values["updated_at"] = values["updated_at"] or now
    return values


class AsyncStoreSubscriptionRepository(StoreSubscriptionRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(
        self,
        user_id: str,
        *,
        entitlement_key: str | None = None,
        include_absent: bool = True,
    ) -> list[StoreSubscription]:
        statement = select(Row).where(Row.user_id == user_id)
        if entitlement_key is not None:
            statement = statement.where(Row.entitlement_key == entitlement_key)
        if not include_absent:
            statement = statement.where(Row.state != StoreSubscriptionState.ABSENT)
        return await self._list(statement.order_by(Row.created_at, Row.id))

    async def list_for_customer(
        self,
        provider: str,
        source_customer_id: str,
        *,
        user_id: str | None = None,
    ) -> list[StoreSubscription]:
        statement = select(Row).where(
            Row.provider == provider,
            Row.source_customer_id == source_customer_id,
        )
        if user_id is not None:
            statement = statement.where(Row.user_id == user_id)
        return await self._list(statement.order_by(Row.created_at, Row.id))

    async def get_by_keys(
        self, provider: str, keys: Sequence[tuple[str, str]]
    ) -> list[StoreSubscription]:
        if not keys:
            return []
        statement = select(Row).where(
            Row.provider == provider,
            tuple_(Row.environment, Row.subscription_key).in_(list(keys)),
        )
        return await self._list(statement.order_by(Row.id))

    async def lock_keys(self, provider: str, keys: Sequence[tuple[str, str]]) -> None:
        if not keys:
            return
        names = sorted({f"{provider}:{env}:{key}" for env, key in keys})
        # Lock in hash order: the hash is the lock, so this order is global.
        hashes = await self.session.execute(_KEY_HASHES, {"keys": names})
        for (key_hash,) in hashes.all():
            await self.session.execute(
                _LOCK_KEY, {"space": _KEY_LOCK_SPACE, "hash": key_hash}
            )

    async def upsert(self, row: StoreSubscription) -> StoreSubscription:
        return (await self._upsert([row], returning=True))[0]

    async def upsert_many(self, rows: Sequence[StoreSubscription]) -> None:
        await self._upsert(rows, returning=False)

    async def _upsert(
        self, rows: Sequence[StoreSubscription], *, returning: bool
    ) -> list[StoreSubscription]:
        # One statement cannot touch the same key twice; the last row wins.
        by_key = {(r.provider, r.environment, r.subscription_key): r for r in rows}
        if not by_key:
            return []
        insert = pg_insert(Row).values([_values(row) for row in by_key.values()])
        statement = insert.on_conflict_do_update(
            constraint="uq_ss_key",
            set_={column: insert.excluded[column] for column in _UPDATE_COLUMNS},
        )
        if not returning:
            await self.session.execute(statement)
            return []
        result = await self.session.execute(
            statement.returning(*[getattr(Row, column) for column in _COLUMNS])
        )
        return [_to_domain(row) for row in result.all()]

    async def _list(self, statement) -> list[StoreSubscription]:
        rows = await self.session.execute(
            statement.execution_options(populate_existing=True)
        )
        return [_to_domain(row) for row in rows.scalars()]
