"""Composition root for referral command handlers."""

from src.app.handlers.command_handlers.referral.apply_referral_code_handler import (
    ApplyReferralCodeCommandHandler,
)
from src.bootstrap.integration_services import (
    get_affiliate_service,
    get_integration_event_publisher,
)
from src.infra.config.settings import get_settings
from src.infra.database.uow_async import AsyncUnitOfWork


def get_apply_referral_code_handler() -> ApplyReferralCodeCommandHandler:
    """Build the referral handler with infrastructure dependencies composed."""
    settings = get_settings()
    event_publisher = (
        get_integration_event_publisher()
        if settings.AFFILIATE_INTEGRATION_ENABLED
        else None
    )
    return ApplyReferralCodeCommandHandler(
        uow=AsyncUnitOfWork(),
        event_publisher=event_publisher,
        affiliate_service=get_affiliate_service(
            enabled=settings.AFFILIATE_INTEGRATION_ENABLED
        ),
        affiliate_enabled=settings.AFFILIATE_INTEGRATION_ENABLED,
        environment=settings.ENVIRONMENT,
    )
