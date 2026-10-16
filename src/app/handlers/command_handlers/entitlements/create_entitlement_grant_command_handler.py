"""Write a manual grant to the ledger and recompute the user's projection."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.entitlements.create_entitlement_grant_command import (
    CreateEntitlementGrantCommand,
)
from src.app.schemas.entitlement_schemas import EntitlementGrantResult
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.events.base import EventHandler, handles
from src.domain.exceptions.billing_exceptions import InvalidEntitlementGrant
from src.domain.model.billing.ledger import EntitlementGrant, validate_grant_request
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.utils.timezone_utils import utc_now
from src.observability import increment_metric

logger = logging.getLogger(__name__)


@handles(CreateEntitlementGrantCommand)
class CreateEntitlementGrantCommandHandler(
    EventHandler[CreateEntitlementGrantCommand, EntitlementGrantResult]
):
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        sync_service: EntitlementSyncService,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._uow_factory = uow_factory
        self._sync = sync_service
        self._clock = clock
        self._new_id = id_factory

    async def handle(
        self, command: CreateEntitlementGrantCommand
    ) -> EntitlementGrantResult:
        now = self._clock()
        starts_at = command.starts_at or now
        try:
            validate_grant_request(
                source=command.source,
                reason=command.reason,
                starts_at=starts_at,
                ends_at=command.ends_at,
                now=now,
            )
        except InvalidEntitlementGrant as exc:
            raise ValidationException(str(exc)) from exc
        async with self._uow_factory() as uow:
            user = await uow.users.find_by_id(command.user_id)  # type: ignore[arg-type]
            if user is None or not user.is_active:
                raise ResourceNotFoundException(f"User {command.user_id} not found")
            await uow.user_entitlements.lock_users([command.user_id])

            if command.source_ref is not None:
                existing = await uow.entitlement_grants.find_by_source_ref(
                    command.source, command.source_ref, command.entitlement_key
                )
                if existing is not None:
                    if existing.user_id != command.user_id:
                        raise ConflictException(
                            "source_ref is already used by another user's grant"
                        )
                    return EntitlementGrantResult(
                        grant=existing,
                        projection=await uow.user_entitlements.get(
                            command.user_id, command.entitlement_key
                        ),
                        changed=False,
                    )

            grant = await uow.entitlement_grants.add(
                EntitlementGrant(
                    id=self._new_id(),
                    user_id=command.user_id,
                    entitlement_key=command.entitlement_key,
                    source=command.source,
                    source_ref=command.source_ref,
                    reason=command.reason.strip(),
                    starts_at=starts_at,
                    ends_at=command.ends_at,
                    created_by=command.created_by,
                    created_at=now,
                )
            )
            projections = await self._sync.recompute_locked(
                uow, command.user_id, now=now
            )

        increment_metric(
            "entitlement.grant.created",
            attributes={
                "source": grant.source,
                "entitlement_key": grant.entitlement_key,
            },
        )
        # created_by (an admin email) stays on the row; emails are never logged.
        logger.warning(
            "Entitlement grant created",
            extra={
                "grant_id": grant.id,
                "user_id": grant.user_id,
                "entitlement_key": grant.entitlement_key,
                "source": grant.source,
                "ends_at": grant.ends_at.isoformat(),
            },
        )
        return EntitlementGrantResult(
            grant=grant, projection=projections.get(grant.entitlement_key)
        )
