"""Device push tokens stored in ``user_fcm_tokens``."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.notification import DeviceType, PushTarget
from src.domain.ports.push_token_repository_port import PushTokenRepositoryPort
from src.infra.database.models.notification.user_fcm_token import (
    UserFcmTokenORM as Token,
)

# Newest devices first; older registrations are almost always stale installs.
_MAX_TARGETS_PER_USER = 10


class AsyncPushTokenRepository(PushTokenRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(
        self, *, user_id: str, token: str, device_type: DeviceType
    ) -> None:
        # A device belongs to whoever registered it last (account switch on
        # the same phone), so the token moves instead of being duplicated.
        statement = pg_insert(Token).values(
            id=str(uuid4()),
            user_id=user_id,
            fcm_token=token,
            device_type=device_type.value,
            is_active=True,
            last_registered_at=func.now(),
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Token.fcm_token],
                set_={
                    "user_id": statement.excluded.user_id,
                    "device_type": statement.excluded.device_type,
                    "is_active": True,
                    "last_registered_at": func.now(),
                    "updated_at": func.now(),
                },
            )
        )

    async def deactivate(self, *, user_id: str, token: str) -> bool:
        result = await self.session.execute(
            update(Token)
            .where(Token.user_id == user_id, Token.fcm_token == token)
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def list_active_targets(self, *, user_id: str) -> list[PushTarget]:
        rows = await self.session.execute(
            select(Token.fcm_token, Token.device_type)
            .where(
                Token.user_id == user_id,
                Token.is_active.is_(True),
                # Rows from before this API existed were never verified.
                Token.last_registered_at.is_not(None),
            )
            .order_by(Token.last_registered_at.desc())
            .limit(_MAX_TARGETS_PER_USER)
        )
        return [
            PushTarget(token=token, device_type=DeviceType(device_type))
            for token, device_type in rows.all()
        ]

    async def deactivate_tokens(self, tokens: Sequence[str]) -> int:
        unique = list(dict.fromkeys(tokens))
        if not unique:
            return 0
        result = await self.session.execute(
            update(Token)
            .where(Token.fcm_token.in_(unique), Token.is_active.is_(True))
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)
