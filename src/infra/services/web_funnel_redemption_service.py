"""Infrastructure persistence for web-funnel redemptions."""

from sqlalchemy import func, select

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_completion import finalize_redemption
from src.infra.services.web_funnel_redemption_identity import (
    RedemptionIdentityUnavailable,
    WebFunnelRedemptionIdentityService,
)

_TERMINAL_LEAD_STATUSES = frozenset({"refunded", "revoked", "conflict", "expired"})


class WebFunnelRedemptionService:
    """Database adapter used from the API composition root."""

    async def finalize(self, *args, **kwargs):
        return await finalize_redemption(*args, **kwargs)

    async def preflight(
        self, db, *, uid: str, email: str, redemption_link_hash: str
    ) -> bool:
        """Bind a matching verified Firebase identity before consuming the link."""
        binding = await db.scalar(
            select(WebFunnelRedemption)
            .where(WebFunnelRedemption.redemption_link_hash == redemption_link_hash)
            .with_for_update()
        )
        if not binding or binding.finalized_uid:
            return False
        if binding.preflight_uid and binding.preflight_uid != uid:
            return False
        if binding.redeemer_uid and binding.redeemer_uid != uid:
            return False
        lead = await db.get(WebFunnelLead, binding.lead_id, with_for_update=True)
        if not lead or lead.status in _TERMINAL_LEAD_STATUSES:
            return False
        if not lead.email.strip().lower() == email.strip().lower():
            return False
        binding.preflight_uid = uid
        binding.preflight_at = utc_now()
        await db.commit()
        return True

    async def start_silent_login(
        self,
        db,
        *,
        redemption_link_hash: str,
        identity: WebFunnelRedemptionIdentityService,
    ) -> str | None:
        """Mint a retryable custom token for an unconsumed redemption link.

        ``None`` hides an unknown, consumed or terminal link and an identity
        outage behind the same not-found response. Nothing is recorded then.
        """
        binding = await db.scalar(
            select(WebFunnelRedemption)
            .where(WebFunnelRedemption.redemption_link_hash == redemption_link_hash)
            .with_for_update()
        )
        if not binding or binding.finalized_uid:
            return None
        lead = await db.get(WebFunnelLead, binding.lead_id, with_for_update=True)
        if not lead or lead.status in _TERMINAL_LEAD_STATUSES:
            return None
        owner = await db.scalar(
            select(User).where(func.lower(User.email) == lead.email.strip().lower())
        )
        try:
            token = await identity.mint_for_email(
                lead.email, mealtrack_uid=owner.firebase_uid if owner else None
            )
        except RedemptionIdentityUnavailable:
            return None
        binding.silent_login_minted_at = utc_now()
        binding.silent_login_generation = (binding.silent_login_generation or 0) + 1
        await db.commit()
        return token

    async def record_webhook_redemption(self, db, event: dict) -> bool:
        if event.get("type") != "PURCHASE_REDEEMED":
            return False
        originals = _event_values(event, "redeemed_from")
        original = event.get("original_app_user_id")
        if isinstance(original, str):
            originals.add(original)
        redeemers = _event_values(event, "redeemed_by")
        if not originals or not redeemers:
            return False
        environment = event.get("environment")
        if not isinstance(environment, str):
            return False
        binding = await db.scalar(
            select(WebFunnelRedemption)
            .where(
                WebFunnelRedemption.original_app_user_id.in_(originals),
                func.lower(WebFunnelRedemption.environment) == environment.lower(),
            )
            .with_for_update()
        )
        if not binding:
            return False
        existing_aliases = set(binding.provider_app_user_ids or [])
        binding.provider_app_user_ids = sorted(existing_aliases | redeemers)
        binding.redemption_confirmed_at = utc_now()
        return True


def _event_values(event: dict, name: str) -> set[str]:
    value = event.get(name)
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()
