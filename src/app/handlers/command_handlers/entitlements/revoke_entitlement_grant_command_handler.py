"""Revoke a manual grant and recompute the user's projection."""

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.entitlements.revoke_entitlement_grant_command import (
    RevokeEntitlementGrantCommand,
)
from src.app.schemas.entitlement_schemas import EntitlementGrantResult
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.events.base import EventHandler, handles
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.utils.timezone_utils import utc_now
from src.observability import increment_metric

logger = logging.getLogger(__name__)


@handles(RevokeEntitlementGrantCommand)
class RevokeEntitlementGrantCommandHandler(
    EventHandler[RevokeEntitlementGrantCommand, EntitlementGrantResult]
):
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        sync_service: EntitlementSyncService,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._sync = sync_service
        self._clock = clock

    async def handle(
        self, command: RevokeEntitlementGrantCommand
    ) -> EntitlementGrantResult:
        now = self._clock()
        async with self._uow_factory() as uow:
            grant = await uow.entitlement_grants.get(command.grant_id)
            if grant is None:
                raise ResourceNotFoundException(f"Grant {command.grant_id} not found")
            await uow.user_entitlements.lock_users([grant.user_id])
            if grant.is_revoked:
                return EntitlementGrantResult(
                    grant=grant,
                    projection=await uow.user_entitlements.get(
                        grant.user_id, grant.entitlement_key
                    ),
                    changed=False,
                )
            revoked = await uow.entitlement_grants.revoke(
                grant.id,
                revoked_at=now,
                reason=command.reason.strip(),
                revoked_by=command.revoked_by.strip(),
            )
            if revoked is None:  # pragma: no cover - deleted under our lock
                raise ResourceNotFoundException(f"Grant {command.grant_id} not found")
            projections = await self._sync.recompute_locked(uow, grant.user_id, now=now)

        increment_metric(
            "entitlement.grant.revoked",
            attributes={
                "source": revoked.source,
                "entitlement_key": revoked.entitlement_key,
            },
        )
        # ``revoked_by`` is stored on the grant; the log carries only a digest
        # because emails are never logged.
        logger.warning(
            "Entitlement grant revoked",
            extra={
                "grant_id": revoked.id,
                "user_id": revoked.user_id,
                "revoked_by_sha256": hashlib.sha256(
                    command.revoked_by.strip().lower().encode("utf-8")
                ).hexdigest(),
            },
        )
        return EntitlementGrantResult(
            grant=revoked, projection=projections.get(revoked.entitlement_key)
        )
