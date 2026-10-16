"""Composition root for the entitlement ledger (NM-680).

Process-wide singletons: one RevenueCat budget and one feature-flag cache per
process, so every request and worker loop shares them.
"""

from src.app.handlers.command_handlers.entitlements import (
    CreateEntitlementGrantCommandHandler,
    RevokeEntitlementGrantCommandHandler,
)
from src.app.services.entitlement_account_service import (
    HINTED_SYNC_LIMIT,
    DelayedResyncScheduler,
    EntitlementAccountService,
    SlidingWindowRateLimiter,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import (
    EntitlementAccessConfig,
    EntitlementSyncService,
)
from src.domain.services.billing.access_policy import parse_user_id_allowlist
from src.infra.adapters.revenuecat_billing_provider import RevenueCatBillingProvider
from src.infra.config.settings import get_settings
from src.infra.database import config_async
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.feature_flag_reader import FeatureFlagReader
from src.infra.services.rc_budget import RcBudget

_rc_budget: RcBudget | None = None
_feature_flag_reader: FeatureFlagReader | None = None
_resync_scheduler: DelayedResyncScheduler | None = None
_sync_rate_limiter: SlidingWindowRateLimiter | None = None
_hint_rate_limiter: SlidingWindowRateLimiter | None = None


def _session_factory():
    factory = config_async.AsyncSessionLocal
    if factory is None:
        raise RuntimeError("AsyncSessionLocal is not initialized")
    return factory()


def get_rc_budget() -> RcBudget:
    global _rc_budget
    if _rc_budget is None:
        settings = get_settings()
        _rc_budget = RcBudget(
            rate_per_second=settings.ENTITLEMENT_RC_RPS,
            concurrency=settings.ENTITLEMENT_RC_CONCURRENCY,
        )
    return _rc_budget


def get_feature_flag_reader() -> FeatureFlagReader:
    global _feature_flag_reader
    if _feature_flag_reader is None:
        _feature_flag_reader = FeatureFlagReader(_session_factory)
    return _feature_flag_reader


def get_entitlement_access_config() -> EntitlementAccessConfig:
    settings = get_settings()
    return EntitlementAccessConfig(
        sandbox_policy=settings.ENTITLEMENT_SANDBOX_POLICY,
        sandbox_allowed_user_ids=parse_user_id_allowlist(
            settings.ENTITLEMENT_SANDBOX_ALLOWED_USER_IDS
        ),
    )


def get_entitlement_sync_service() -> EntitlementSyncService:
    return EntitlementSyncService(
        uow_factory=AsyncUnitOfWork,
        provider=RevenueCatBillingProvider(
            api_key=get_settings().REVENUECAT_SECRET_API_KEY
        ),
        budget=get_rc_budget(),
        access_config=get_entitlement_access_config(),
    )


def get_entitlement_query_service() -> EntitlementQueryService:
    return EntitlementQueryService(
        uow_factory=AsyncUnitOfWork,
        sync_service=get_entitlement_sync_service(),
        flags=get_feature_flag_reader(),
    )


def get_entitlement_inbox_service() -> EntitlementInboxService:
    return EntitlementInboxService(
        uow_factory=AsyncUnitOfWork, sync_service=get_entitlement_sync_service()
    )


async def _follow_up_sync(user_id: str) -> None:
    await get_entitlement_account_service().follow_up_sync(user_id)


def get_resync_scheduler() -> DelayedResyncScheduler:
    global _resync_scheduler
    if _resync_scheduler is None:
        _resync_scheduler = DelayedResyncScheduler(_follow_up_sync)
    return _resync_scheduler


def get_sync_rate_limiter() -> SlidingWindowRateLimiter:
    global _sync_rate_limiter
    if _sync_rate_limiter is None:
        _sync_rate_limiter = SlidingWindowRateLimiter()
    return _sync_rate_limiter


def get_hint_rate_limiter() -> SlidingWindowRateLimiter:
    global _hint_rate_limiter
    if _hint_rate_limiter is None:
        _hint_rate_limiter = SlidingWindowRateLimiter(limit=HINTED_SYNC_LIMIT)
    return _hint_rate_limiter


def get_entitlement_account_service() -> EntitlementAccountService:
    sync_service = get_entitlement_sync_service()
    flags = get_feature_flag_reader()
    return EntitlementAccountService(
        query_service=EntitlementQueryService(
            uow_factory=AsyncUnitOfWork, sync_service=sync_service, flags=flags
        ),
        sync_service=sync_service,
        inbox_service=EntitlementInboxService(
            uow_factory=AsyncUnitOfWork, sync_service=sync_service
        ),
        flags=flags,
        scheduler=get_resync_scheduler(),
        rate_limiter=get_sync_rate_limiter(),
        hint_rate_limiter=get_hint_rate_limiter(),
    )


def get_create_entitlement_grant_handler() -> CreateEntitlementGrantCommandHandler:
    return CreateEntitlementGrantCommandHandler(
        uow_factory=AsyncUnitOfWork, sync_service=get_entitlement_sync_service()
    )


def get_revoke_entitlement_grant_handler() -> RevokeEntitlementGrantCommandHandler:
    return RevokeEntitlementGrantCommandHandler(
        uow_factory=AsyncUnitOfWork, sync_service=get_entitlement_sync_service()
    )


def reset_entitlement_singletons_for_tests() -> None:
    global _rc_budget, _feature_flag_reader, _resync_scheduler, _sync_rate_limiter
    global _hint_rate_limiter
    _rc_budget = None
    _feature_flag_reader = None
    _resync_scheduler = None
    _sync_rate_limiter = None
    _hint_rate_limiter = None
