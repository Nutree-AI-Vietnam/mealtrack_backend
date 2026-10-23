"""Plan reminders reach only open, subscribed English leads and carry a private link."""

from datetime import UTC, datetime, timedelta

import pytest

from src.app.services import web_funnel_plan_reminders as reminders
from src.app.services.web_funnel_claim_common import hash_secret
from src.infra.database.models.web_funnel_claim import WebFunnelLead, WebFunnelOutbox


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(reminders.settings, "WEB_FUNNEL_PLAN_REMINDERS_ENABLED", True)
    monkeypatch.setattr(
        reminders.settings, "WEB_FUNNEL_PLAN_LINK_BASE_URL", "https://quiz.example/plan"
    )
    monkeypatch.setattr(
        reminders.settings,
        "WEB_FUNNEL_UNSUBSCRIBE_BASE_URL",
        "https://quiz.example/unsubscribe",
    )
    monkeypatch.setattr(
        reminders.settings, "WEB_FUNNEL_UNSUBSCRIBE_SECRET", "unsubscribe-secret"
    )


def _lead(**overrides) -> WebFunnelLead:
    values = {
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "sam@example.com",
        "access_key_hash": "hash",
        "request_id": "request",
        "snapshot_version": "v1",
        "snapshot": {"weight": 81.6466, "target_weight_kg": 70.307},
        "snapshot_hash": "snapshot",
        "status": "draft",
        "revision": 1,
        "access_sync_status": "pending",
        "locale": "en",
        "display_units": "imperial",
        "created_at": datetime(2026, 10, 23, tzinfo=UTC),
    }
    values.update(overrides)
    return WebFunnelLead(**values)


def _outbox(sequence: int = 1) -> WebFunnelOutbox:
    return WebFunnelOutbox(
        idempotency_key=f"plan_reminder:{sequence}:lead",
        job_type="plan_reminder",
        payload={
            "lead_id": "11111111-1111-1111-1111-111111111111",
            "sequence": sequence,
        },
        status="pending",
        attempts=0,
        next_attempt_at=datetime(2026, 10, 23, tzinfo=UTC),
    )


class Session:
    def __init__(self, lead):
        self.lead = lead
        self.commits = 0

    async def get(self, _model, _id, **_kwargs):
        return self.lead

    async def commit(self):
        self.commits += 1


def test_goal_summary_matches_the_web_funnel_date_and_units():
    assert reminders.goal_summary(_lead()) == ("155 lb", "Mar 31")
    assert reminders.goal_summary(_lead(display_units="metric")) == ("70 kg", "Mar 31")
    assert (
        reminders.goal_summary(_lead(snapshot={"weight": 60, "target_weight_kg": 60}))
        is None
    )


def test_three_reminders_are_queued_at_one_hour_one_day_and_three_days():
    created = datetime(2026, 10, 23, tzinfo=UTC)
    rows = reminders.reminder_outbox_rows("lead", created)
    assert [row.idempotency_key for row in rows] == [
        "plan_reminder:1:lead",
        "plan_reminder:2:lead",
        "plan_reminder:3:lead",
    ]
    assert [row.next_attempt_at - created for row in rows] == [
        timedelta(hours=1),
        timedelta(days=1),
        timedelta(days=3),
    ]


def test_email_keeps_the_token_in_the_fragment_and_links_unsubscribe():
    subject, html = reminders.reminder_email(_lead(), 1, "secret-token")
    assert subject == "Your plan to reach 155 lb by Mar 31 is saved"
    assert "https://quiz.example/plan#t=secret-token" in html
    assert "https://quiz.example/plan#t=secret-token&amp;start=1" in html
    assert (
        "https://quiz.example/unsubscribe#lead=11111111-1111-1111-1111-111111111111&amp;sig="
        in html
    )
    assert "support@nutreeai.com" in html


def test_recovery_email_names_the_currency_fix():
    subject, html = reminders.reminder_email(_lead(), 0, "secret-token")
    assert subject == "Your Nutree plan is ready, now in US dollars"
    assert "wrong currency" in html


def test_unsubscribe_signature_is_bound_to_the_lead():
    signature = reminders.unsubscribe_signature("lead-a")
    assert reminders.unsubscribe_signature_matches("lead-a", signature)
    assert not reminders.unsubscribe_signature_matches("lead-b", signature)


@pytest.mark.asyncio
async def test_reminder_stores_only_the_hash_of_the_emailed_token():
    session, outbox, sent = Session(_lead()), _outbox(), []

    async def send(email, subject, html):
        sent.append((email, subject, html))
        return True

    await reminders.process_plan_reminder(session, outbox, send)
    email, _subject, html = sent[0]
    token = html.split("plan#t=")[1].split('"')[0]
    assert email == "sam@example.com"
    assert session.lead.plan_link_token_hash == hash_secret(token)
    assert session.lead.plan_link_expires_at is not None
    assert outbox.status == "completed"


@pytest.mark.parametrize(
    "lead",
    [
        _lead(status="payment_verified"),
        _lead(reminders_unsubscribed_at=datetime(2026, 10, 23, tzinfo=UTC)),
        _lead(locale="vi"),
        None,
    ],
)
@pytest.mark.asyncio
async def test_paid_unsubscribed_or_non_english_leads_get_nothing(lead):
    outbox = _outbox()

    async def send(*_args):
        raise AssertionError("must not send")

    await reminders.process_plan_reminder(Session(lead), outbox, send)
    assert outbox.status == "completed"


@pytest.mark.asyncio
async def test_failed_send_backs_off_then_gives_up():
    outbox = _outbox()

    async def send(*_args):
        return False

    for _ in range(5):
        await reminders.process_plan_reminder(Session(_lead()), outbox, send)
    assert outbox.attempts == 5
    assert outbox.status == "failed"
