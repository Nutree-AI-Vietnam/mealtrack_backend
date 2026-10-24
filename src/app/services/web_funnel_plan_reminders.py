"""Reminder emails that bring unpaid web-funnel leads back to their saved plan.

Each email mints a fresh private plan link. Only the link's hash is stored, and
the raw token travels in the URL fragment so it never reaches a server log.
"""

import hashlib
import hmac
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.web_funnel_claim_common import hash_secret, new_secret, utcnow
from src.infra.config.settings import settings
from src.infra.database.models.web_funnel_claim import WebFunnelLead, WebFunnelOutbox

PLAN_LINK_TTL = timedelta(days=7)
# Sequence 0 is the one-off recovery email; 1-3 follow a new lead.
REMINDER_DELAYS = {1: timedelta(hours=1), 2: timedelta(days=1), 3: timedelta(days=3)}
REMINDER_JOB = "plan_reminder"
OPEN_STATUSES = {"draft", "checkout_started"}
KG_PER_WEEK = 0.5
KG_PER_POUND = 0.45359237
SUPPORT_EMAIL = "support@nutreeai.com"

SendEmail = Callable[[str, str, str], Awaitable[bool]]


def reminder_outbox_rows(lead_id: str, created_at: datetime) -> list[WebFunnelOutbox]:
    """The three timed reminders for a new lead, idempotent per lead and step."""
    return [
        WebFunnelOutbox(
            idempotency_key=f"{REMINDER_JOB}:{sequence}:{lead_id}",
            job_type=REMINDER_JOB,
            payload={"lead_id": lead_id, "sequence": sequence},
            status="pending",
            attempts=0,
            next_attempt_at=created_at + delay,
        )
        for sequence, delay in REMINDER_DELAYS.items()
    ]


def unsubscribe_signature(lead_id: str) -> str:
    secret = (
        settings.WEB_FUNNEL_UNSUBSCRIBE_SECRET or settings.WEB_FUNNEL_BFF_SHARED_SECRET
    )
    return hmac.new(
        secret.encode("utf-8"), lead_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def unsubscribe_signature_matches(lead_id: str, signature: str) -> bool:
    if not (
        settings.WEB_FUNNEL_UNSUBSCRIBE_SECRET or settings.WEB_FUNNEL_BFF_SHARED_SECRET
    ):
        return False
    return hmac.compare_digest(unsubscribe_signature(lead_id), signature)


def plan_link(token: str, *, start: bool = False) -> str:
    fragment = {"t": token, **({"start": "1"} if start else {})}
    return f"{settings.WEB_FUNNEL_PLAN_LINK_BASE_URL}#{urlencode(fragment)}"


def unsubscribe_link(lead_id: str) -> str:
    fragment = urlencode({"lead": lead_id, "sig": unsubscribe_signature(lead_id)})
    return f"{settings.WEB_FUNNEL_UNSUBSCRIBE_BASE_URL}#{fragment}"


def _weight(kg: float, units: str | None) -> str:
    if units == "imperial":
        return f"{round(kg / KG_PER_POUND)} lb"
    return f"{round(kg)} kg"


def goal_summary(lead: WebFunnelLead) -> tuple[str, str] | None:
    """Target weight and goal date with the same pace the web funnel shows."""
    snapshot = lead.snapshot or {}
    current, target = snapshot.get("weight"), snapshot.get("target_weight_kg")
    if not isinstance(current, (int, float)) or not isinstance(target, (int, float)):
        return None
    gap = abs(float(current) - float(target))
    if gap <= 0.5:
        return None
    days = max(4, math.ceil(gap / KG_PER_WEEK * 7))
    start = lead.created_at or utcnow()
    goal_date = start + timedelta(days=days)
    return _weight(float(target), lead.display_units), f"{goal_date:%b} {goal_date.day}"


def reminder_email(lead: WebFunnelLead, sequence: int, token: str) -> tuple[str, str]:
    """Subject and HTML for one reminder; every value is escaped."""
    goal = goal_summary(lead)
    target = f"{goal[0]} by {goal[1]}" if goal else None
    subjects = {
        0: "Your Nutree plan is ready, now in US dollars",
        1: f"Your plan to reach {target} is saved"
        if target
        else "Your Nutree plan is saved",
        2: "Your first week starts with three small changes",
        3: "Last reminder: your Nutree plan is still saved",
    }
    intros = {
        0: "When you built your plan, our checkout showed the wrong currency or no plans at all, so you could not finish. That is fixed: prices are now in US dollars.",
        1: "Your calorie and protein targets, a sample day and your first three changes are ready.",
        2: "Most people stall because they try to change everything at once. Your plan starts with three small changes built from your answers.",
        3: "We will stop reminding you after this email. Your plan stays one tap away for the next 7 days.",
    }
    headline = f"Reach {escape(target)}" if target else "Your Nutree plan"
    button = "display:block;border-radius:14px;padding:14px 18px;text-align:center;font-weight:800;text-decoration:none"
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;color:#17453a">
<p style="font-size:13px;font-weight:700;letter-spacing:.06em;color:#1fa892">YOUR NUTREE PLAN</p>
<h1 style="font-size:26px;line-height:1.2;margin:4px 0 12px">{headline}</h1>
<p style="font-size:15px;line-height:1.55;color:#33504a">{escape(intros.get(sequence, intros[1]))}</p>
<a href="{escape(plan_link(token), quote=True)}" style="{button};background:#17453a;color:#fff;margin:22px 0 10px">See my plan</a>
<a href="{escape(plan_link(token, start=True), quote=True)}" style="{button};border:1px solid #cfe0d9;color:#17453a">Start my plan now</a>
<p style="font-size:12px;line-height:1.5;color:#6b7b75;margin-top:22px">Estimate. Results vary. This link is private to you and works for 7 days.</p>
<p style="font-size:12px;line-height:1.5;color:#6b7b75">You get this email because you saved a plan on quiz.nutreeai.com. <a href="{escape(unsubscribe_link(lead.id), quote=True)}" style="color:#6b7b75">Unsubscribe</a> · {SUPPORT_EMAIL}</p>
</div>"""
    return subjects.get(sequence, subjects[1]), html


def reminders_configured() -> bool:
    return bool(
        settings.WEB_FUNNEL_PLAN_REMINDERS_ENABLED
        and settings.WEB_FUNNEL_PLAN_LINK_BASE_URL
        and settings.WEB_FUNNEL_UNSUBSCRIBE_BASE_URL
        and (
            settings.WEB_FUNNEL_UNSUBSCRIBE_SECRET
            or settings.WEB_FUNNEL_BFF_SHARED_SECRET
        )
    )


async def process_plan_reminder(
    db: AsyncSession, outbox: WebFunnelOutbox, send: SendEmail
) -> None:
    """Send one reminder, or close the job when the lead paid, left or opted out."""
    lead = await db.get(WebFunnelLead, outbox.payload["lead_id"], with_for_update=True)
    if (
        not lead
        or lead.status not in OPEN_STATUSES
        or lead.reminders_unsubscribed_at is not None
        or lead.locale != "en"
    ):
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return
    token = new_secret()
    subject, html = reminder_email(lead, int(outbox.payload.get("sequence", 1)), token)
    # Store the hash before sending, so a crash after the send cannot leave a dead link
    # in someone's inbox. The newest emailed link is the one that works.
    lead.plan_link_token_hash = hash_secret(token)
    lead.plan_link_expires_at = utcnow() + PLAN_LINK_TTL
    await db.commit()
    if not await send(lead.email, subject, html):
        outbox.attempts += 1
        if outbox.attempts >= 5:
            outbox.status, outbox.last_error = "failed", "send failed"
        outbox.next_attempt_at = utcnow() + timedelta(
            minutes=min(60, 2**outbox.attempts)
        )
        await db.commit()
        return
    outbox.status, outbox.completed_at = "completed", utcnow()
    await db.commit()
