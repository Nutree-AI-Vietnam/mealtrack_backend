"""Refresh one user's access rows from their RevenueCat customer.

The customer is read before the write transaction, so no database lock is
held across the HTTP call; the snapshot is then applied with
sync_revenuecat_access inside one unit of work, which locks the user row
and commits on success.
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from src.domain.ports.subscription_access_refresh_port import (
    REFRESH_NOT_APPLIED,
    SubscriptionAccessApplyError,
    SubscriptionAccessRefresh,
    SubscriptionAccessRefreshPort,
)
from src.domain.ports.subscription_service_port import (
    SNAPSHOT_REASON_OWNER_CHANGED,
    RevenueCatSnapshotUnavailable,
    SubscriptionServicePort,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.subscription_access_sync_service import (
    record_snapshot_unavailable,
    sync_revenuecat_access,
)
from src.observability import capture_exception
from src.subscription_access_alerts import ALERT_SYNC_APPLY_ERROR, alert_fingerprint

logger = logging.getLogger(__name__)

REFRESH_SOURCE = "refresh"


class RevenueCatAccessRefreshService(SubscriptionAccessRefreshPort):
    """Self-heal for the database access authority, one user at a time.

    Rows written by other providers are never touched: the sync only reads
    and writes provider='revenuecat' rows.
    """

    def __init__(
        self,
        subscription_service_factory: Callable[[], SubscriptionServicePort],
        uow_factory: Callable[[], Any] = AsyncUnitOfWork,
        sync: Callable[..., Any] = sync_revenuecat_access,
    ):
        self._subscription_service_factory = subscription_service_factory
        self._uow_factory = uow_factory
        self._sync = sync

    async def refresh(self, user_id: str) -> SubscriptionAccessRefresh:
        firebase_uid = await self._firebase_uid(user_id)
        if not firebase_uid:
            # The caller authenticated a moment ago; the account was deleted
            # or lost its Firebase identity since. Nothing to read.
            return self._not_applied(
                RevenueCatSnapshotUnavailable(
                    "Refreshed user has no active Firebase identity",
                    reason=SNAPSHOT_REASON_OWNER_CHANGED,
                ),
                user_id,
            )

        try:
            snapshot = await self._subscription_service_factory().get_verified_snapshot(
                firebase_uid
            )
        except RevenueCatSnapshotUnavailable as error:
            record_snapshot_unavailable(error, source=REFRESH_SOURCE, user_id=user_id)
            raise

        try:
            async with self._uow_factory() as uow:
                user = await uow.session.get(User, user_id)
                if user is None or not user.is_active:
                    raise RevenueCatSnapshotUnavailable(
                        "Refreshed user disappeared",
                        reason=SNAPSHOT_REASON_OWNER_CHANGED,
                    )
                outcome = await self._sync(
                    uow.session, user, snapshot=snapshot, source=REFRESH_SOURCE
                )
        except RevenueCatSnapshotUnavailable as error:
            # Readable but unsafe to apply (owner changed, conflicting facts,
            # absence over a possible grant): the unit of work rolled back and
            # the rows stay as they were. No provider event happened, so
            # nothing is marked unresolved.
            return self._not_applied(error, user_id)
        except Exception as error:
            # A bug or a database failure while applying (or committing) the
            # snapshot. Reported here, inside the event bus, so its fixed
            # fingerprint is the first Sentry event for this exception.
            raise _apply_failed(
                error, source=REFRESH_SOURCE, user_id=user_id
            ) from error
        return SubscriptionAccessRefresh(outcome.result)

    async def _firebase_uid(self, user_id: str) -> str | None:
        async with self._uow_factory() as uow:
            return await uow.session.scalar(
                select(User.firebase_uid).where(
                    User.id == user_id, User.is_active.is_(True)
                )
            )

    @staticmethod
    def _not_applied(
        error: RevenueCatSnapshotUnavailable, user_id: str
    ) -> SubscriptionAccessRefresh:
        record_snapshot_unavailable(error, source=REFRESH_SOURCE, user_id=user_id)
        return SubscriptionAccessRefresh(
            REFRESH_NOT_APPLIED, reason=getattr(error, "reason", None)
        )


def _apply_failed(
    error: Exception, *, source: str, user_id: str
) -> SubscriptionAccessApplyError:
    """Report an unexpected apply failure once; return the error to raise."""
    capture_exception(
        error,
        context={
            "component": "subscription_access",
            "operation": "revenuecat_sync",
            "source": source,
            "user_id": user_id,
            "error_type": type(error).__name__,
            "result": "apply_failed",
        },
        fingerprint=alert_fingerprint(ALERT_SYNC_APPLY_ERROR, source),
    )
    logger.warning(
        "RevenueCat snapshot could not be applied: user_id=%s source=%s error_type=%s",
        user_id,
        source,
        type(error).__name__,
    )
    return SubscriptionAccessApplyError(
        f"RevenueCat snapshot could not be applied ({type(error).__name__})"
    )
