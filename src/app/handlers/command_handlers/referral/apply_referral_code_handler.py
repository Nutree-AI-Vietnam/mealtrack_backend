"""Command handler — record a referred user's code application as a pending conversion."""

import logging
from typing import Any

from src.app.commands.referral.apply_referral_code_command import (
    ApplyReferralCodeCommand,
)
from src.app.events.affiliate.affiliate_events import (
    AffiliateAttributionCreatedEvent,
)
from src.domain.ports.affiliate_service_port import AffiliateServicePort
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)

logger = logging.getLogger(__name__)


class ApplyReferralCodeCommandHandler:
    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        event_publisher: IntegrationEventPublisherPort | None = None,
        affiliate_service: AffiliateServicePort | None = None,
        affiliate_enabled: bool = False,
        environment: str = "development",
        **kwargs: Any,
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.affiliate_service = affiliate_service
        self.affiliate_enabled = affiliate_enabled
        self.environment = environment

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(self, command: ApplyReferralCodeCommand) -> None:
        async with self.uow as uow:
            # ── User-referral path (existing behavior, unchanged) ────────────
            code = await uow.referrals.get_code_by_code(command.code)
            if code:
                if code.user_id == command.user_id:
                    raise ValueError("self_referral")
                existing = await uow.referrals.get_conversion_by_referred_user(
                    command.user_id
                )
                if existing:
                    raise ValueError("already_referred")
                await uow.referrals.create_conversion(
                    referrer_user_id=code.user_id,
                    referred_user_id=command.user_id,
                    code=command.code,
                    discount=command.discount_applied,
                    currency=command.currency,
                )
                logger.info(
                    "Referral conversion created: referrer=%s referred=%s",
                    code.user_id,
                    command.user_id,
                )
                return

            # ── Affiliate path (feature-flagged) ────────────────────────────
            # nutree-affiliate is the source of truth for attribution state.
            # MealTrack sends an event and lets nutree-affiliate enforce dedup.
            if self.affiliate_enabled:
                if self.affiliate_service is None:
                    raise RuntimeError("Affiliate service is required when enabled")
                aff_result = await self.affiliate_service.validate_code(command.code)
                if aff_result.active and aff_result.affiliate_id:
                    event = AffiliateAttributionCreatedEvent(
                        environment=self.environment,
                        aggregate_id=str(command.user_id),
                        data={
                            "mealtrack_user_id": command.user_id,
                            "affiliate_id": aff_result.affiliate_id,
                            "affiliate_code": command.code,
                        },
                    )
                    await require_event_publisher(self.event_publisher).publish(
                        event.to_payload()
                    )
                    return

            raise ValueError("invalid_code")
