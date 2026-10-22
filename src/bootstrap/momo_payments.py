"""Composition root for MoMo one-time premium passes."""

import logging
import os
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

# MoMo's sandbox is allowed only where ENVIRONMENT explicitly names one of these
# stages. Settings default ENVIRONMENT to "development", so the process variable
# is read: an unset, blank or unknown value ("prod", a typo) must not let
# production settle free sandbox payments.
_SANDBOX_ENVIRONMENTS = frozenset({"development", "local", "staging", "test"})


def _open_session():
    if config_async.AsyncSessionLocal is None:
        raise RuntimeError("Database engine is not initialised")
    return config_async.AsyncSessionLocal()


def _momo_credentials() -> MomoCredentials:
    """Outside the sandbox stages MoMo runs in production mode or stays off."""
    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    credentials = MomoCredentials(
        env=settings.MOMO_ENV.strip().lower(),
        partner_code=settings.MOMO_PARTNER_CODE,
        access_key=settings.MOMO_ACCESS_KEY,
        secret_key=settings.MOMO_SECRET_KEY,
        require_production=environment not in _SANDBOX_ENVIRONMENTS,
    )
    if credentials.missing == ["MOMO_ENV"]:
        # Keys are set, so this API would otherwise settle sandbox payments.
        logger.error(
            "MoMo is disabled: ENVIRONMENT=%r allows only MOMO_ENV=production",
            environment,
        )
    elif credentials.missing:
        logger.info("MoMo is off until set: %s", ", ".join(credentials.missing))
    return credentials


@lru_cache(maxsize=1)
def get_momo_pass_service() -> MomoPassService:
    return MomoPassService(
        session_factory=_open_session,
        gateway=MomoGatewayAdapter(_momo_credentials()),
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
    """Grant pending passes on sign-in or app activate; True when one is held.

    Never raises: a database or RevenueCat outage must not block sign-in or
    the app, and the next status read, IPN, sign-in or activate retries.
    """
    try:
        return await get_momo_pass_service().activate(user_id, verified_email)
    except Exception:  # noqa: BLE001
        logger.exception("MoMo pass activation failed")
        return False
