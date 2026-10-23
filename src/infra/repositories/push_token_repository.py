"""Async repository for user FCM push tokens."""

import logging
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.notification.enums import DeviceType
from src.infra.database.models.notification.user_fcm_token import (
    UserFcmTokenORM as Token,
)

logger = logging.getLogger(__name__)


class AsyncPushTokenRepository:
    """Async repository for user_fcm_tokens.  Never calls session.commit()."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        user_id: str,
        token: str,
        device_type: DeviceType,
    ) -> None:
        """Insert or update an FCM token row.

        ``created_at`` is supplied explicitly via ``func.now()`` because the
        ``user_fcm_tokens`` table was created before this ORM model existed and
        the physical database column may lack a ``DEFAULT now()`` clause.
        Omitting it caused a NOT NULL violation (see PYTHON-K3).
        """
        statement = pg_insert(Token).values(
            id=str(uuid4()),
            user_id=user_id,
            fcm_token=token,
            device_type=device_type.value,
            is_active=True,
            created_at=func.now(),
            last_registered_at=func.now(),
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Token.fcm_token],
                set_={
                    "updated_at": func.now(),
                    "user_id": statement.excluded.user_id,
                    "device_type": statement.excluded.device_type,
                    "is_active": True,
                    "last_registered_at": func.now(),
                },
            )
        )
        await self.session.flush()

    async def deactivate(self, *, token: str) -> None:
        """Mark a single FCM token as inactive."""
        await self.session.execute(
            pg_insert(Token)
            .values(
                id=str(uuid4()),
                # user_id / device_type are required by NOT NULL; conflict path
                # overwrites is_active so the placeholder values are not persisted.
                user_id="__deactivate__",
                fcm_token=token,
                device_type=DeviceType.IOS.value,
                is_active=False,
                created_at=func.now(),
                last_registered_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=[Token.fcm_token],
                set_={
                    "updated_at": func.now(),
                    "is_active": False,
                },
            )
        )
        await self.session.flush()
