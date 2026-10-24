"""Claim bounded web-funnel outbox rows without logging secrets or PII."""

from urllib.parse import urlencode

from sqlalchemy import select

from src.app.services.web_funnel_claim_common import utcnow
from src.app.services.web_funnel_claim_payment import (
    process_claim_email,
    process_revenuecat_reconcile,
)
from src.app.services.web_funnel_plan_reminders import (
    REMINDER_JOB,
    process_plan_reminder,
    reminders_configured,
)
from src.infra.adapters.resend_email_adapter import ResendEmailAdapter
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.config.settings import settings
from src.infra.database.config_async import AsyncSessionLocal
from src.infra.database.models.web_funnel_claim import WebFunnelOutbox


def claim_link(lead_id: str, token: str) -> str:
    """Keep raw credentials in the URL fragment, never in an HTTP request."""
    return (
        f"{settings.WEB_FUNNEL_CLAIM_LINK_BASE_URL}#"
        f"{urlencode({'v': 2, 'lead_id': lead_id, 'magic_token': token})}"
    )


async def dispatch_web_funnel_outbox(
    batch_size: int = 25, *, lead_id: str | None = None
) -> int:
    """Process due claim work when the required email link base URL is configured."""
    if AsyncSessionLocal is None:
        return 0
    async with AsyncSessionLocal() as session:
        statement = select(WebFunnelOutbox).where(
            WebFunnelOutbox.status == "pending",
            WebFunnelOutbox.next_attempt_at <= utcnow(),
        )
        if lead_id:
            statement = statement.where(
                WebFunnelOutbox.idempotency_key.like(f"%:{lead_id}%")
            )
        statement = (
            statement.order_by(WebFunnelOutbox.next_attempt_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        rows = (await session.scalars(statement)).all()
        adapter = ResendEmailAdapter()
        completed = 0
        for row in rows:
            if (
                row.job_type == "claim_email"
                and settings.WEB_FUNNEL_LEGACY_CLAIM_ENABLED
                and settings.WEB_FUNNEL_CLAIM_LINK_BASE_URL
            ):

                async def send(email: str, token: str, lead_id: str) -> bool:
                    url = claim_link(lead_id, token)
                    result = await adapter.send_email(
                        email,
                        "Open Nutree",
                        f'<p><a href="{url}">Open Nutree</a></p>',
                        tags=["web-claim"],
                    )
                    return result.success

                await process_claim_email(session, row, send)
                completed += 1
            elif row.job_type == REMINDER_JOB and reminders_configured():

                async def send_reminder(email: str, subject: str, html: str) -> bool:
                    result = await adapter.send_email(
                        email, subject, html, tags=["web-plan-reminder"]
                    )
                    return result.success

                await process_plan_reminder(session, row, send_reminder)
                completed += 1
            elif row.job_type == "revenuecat_reconcile":
                await process_revenuecat_reconcile(session, row, RevenueCatAdapter())
                completed += 1
        return completed
