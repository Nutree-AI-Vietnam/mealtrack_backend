"""Composition for the dedicated entitlement worker process (NM-680, D2)."""

from datetime import timedelta

from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.bootstrap.entitlements import (
    get_entitlement_sync_service,
    get_feature_flag_reader,
)
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.workers.entitlement_worker import EntitlementWorker


def build_entitlement_worker(
    *,
    batch_size: int = 10,
    periodic_batch_size: int = 50,
    lease_seconds: int = 600,
    poll_seconds: float = 5.0,
    periodic_interval_seconds: float = 60.0,
    sweep_interval_seconds: float = 900.0,
    resync_after_hours: float = 24.0,
) -> EntitlementWorker:
    """One worker sharing the process RC budget and flag cache with its syncs."""
    sync_service = get_entitlement_sync_service()
    return EntitlementWorker(
        uow_factory=AsyncUnitOfWork,
        inbox=EntitlementInboxService(
            uow_factory=AsyncUnitOfWork, sync_service=sync_service
        ),
        syncer=sync_service,
        flags=get_feature_flag_reader(),
        batch_size=batch_size,
        periodic_batch_size=periodic_batch_size,
        lease_seconds=lease_seconds,
        poll_seconds=poll_seconds,
        periodic_interval_seconds=periodic_interval_seconds,
        sweep_interval_seconds=sweep_interval_seconds,
        resync_after=timedelta(hours=resync_after_hours),
    )
