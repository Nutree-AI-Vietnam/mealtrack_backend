"""Composition root for MoMo one-time premium passes."""

import logging
from functools import lru_cache

from src.infra.adapters.momo_gateway_adapter import (
    MomoCredentials,
    MomoGatewayAdapter,
    MomoGatewayError,
)
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.config.settings import settings
from src.infra.database import config_async
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_service import (
    MomoBuyer,
    MomoLeadUnavailable,
    MomoNotConfigured,
    MomoPassService,
    MomoUrls,
)

__all__ = [
    "MomoBuyer",
    "MomoGatewayError",
    "MomoLeadUnavailable",
    "MomoNotConfigured",
    "activate_momo_passes",
    "get_momo_pass_service",
]

logger = logging.getLogger(__name__)


def _open_session():
    if config_async.AsyncSessionLocal is None:
        raise RuntimeError("Database engine is not initialised")
    return config_async.AsyncSessionLocal()


@lru_cache(maxsize=1)
def get_momo_pass_service() -> MomoPassService:
    credentials = MomoCredentials(
        env=settings.MOMO_ENV,
        partner_code=settings.MOMO_PARTNER_CODE,
        access_key=settings.MOMO_ACCESS_KEY,
        secret_key=settings.MOMO_SECRET_KEY,
    )
    return MomoPassService(
        session_factory=_open_session,
        gateway=MomoGatewayAdapter(credentials),
        entitlements=MomoPassEntitlements(
            RevenueCatAdapter(settings.REVENUECAT_SECRET_API_KEY or "")
        ),
        urls=MomoUrls(
            ipn_url=settings.MOMO_IPN_URL,
            web_redirect_url=settings.MOMO_WEB_REDIRECT_URL,
            app_redirect_url=settings.MOMO_APP_REDIRECT_URL,
        ),
    )


async def activate_momo_passes(user_id: str, verified_email: str | None) -> bool:
    """Grant pending passes on sign-in; True when the user now holds a pass.

    Never raises: a database or RevenueCat outage must not block sign-in, and
    the next sync retries.
    """
    try:
        return await get_momo_pass_service().activate(user_id, verified_email)
    except Exception:  # noqa: BLE001
        logger.exception("MoMo pass activation failed")
        return False
