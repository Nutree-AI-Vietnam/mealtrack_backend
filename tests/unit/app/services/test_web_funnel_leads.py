"""Claim resend: possession-bound, rate-limited, and committed by the service."""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from src.app.services.web_funnel_claim_common import (
    RESEND_COOLDOWN,
    hash_secret,
    utcnow,
)
from src.app.services.web_funnel_leads import queue_claim_resend
from src.infra.database.models.web_funnel_claim import (
    WebFunnelClaim,
    WebFunnelLead,
    WebFunnelOutbox,
)

ACCESS_KEY = "a" * 32


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class ResendSession:
    def __init__(self, lead, claims=()):
        self.lead = lead
        self.claims = list(claims)
        self.added: list[object] = []
        self.committed = False

    async def get(self, _model, _identifier, **_kwargs):
        return self.lead

    async def scalars(self, statement):
        if "web_funnel_outbox" in str(statement):
            return _Rows([])
        return _Rows(self.claims)

    async def scalar(self, _statement):
        return max((claim.generation for claim in self.claims), default=None)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


def _lead(status="email_queued") -> WebFunnelLead:
    return WebFunnelLead(
        id="lead-1",
        email="buyer@example.com",
        access_key_hash=hash_secret(ACCESS_KEY),
        request_id="request-1",
        snapshot_version="web_onboarding_snapshot_v1",
        snapshot={},
        snapshot_hash="snapshot",
        status=status,
        revision=1,
        access_sync_status="pending",
    )


def _claim(generation: int, *, age: timedelta) -> WebFunnelClaim:
    return WebFunnelClaim(
        lead_id="lead-1",
        generation=generation,
        magic_token_hash=f"hash-{generation}",
        expires_at=utcnow() + timedelta(hours=1),
        created_at=utcnow() - age,
    )


@pytest.mark.asyncio
async def test_resend_revokes_live_links_and_queues_the_next_generation():
    old = _claim(1, age=RESEND_COOLDOWN * 2)
    session = ResendSession(_lead(), [old])

    lead, retry_after = await queue_claim_resend(session, "lead-1", ACCESS_KEY)

    assert lead.status == "email_queued"
    assert retry_after == int(RESEND_COOLDOWN.total_seconds())
    assert old.revoked_at is not None
    [outbox] = session.added
    assert isinstance(outbox, WebFunnelOutbox)
    assert outbox.idempotency_key == "claim-email:lead-1:2"
    assert outbox.payload == {"lead_id": "lead-1", "generation": 2}
    assert session.committed


@pytest.mark.asyncio
async def test_resend_inside_cooldown_changes_nothing():
    recent = _claim(1, age=timedelta(seconds=30))
    session = ResendSession(_lead(), [recent])

    _lead_row, retry_after = await queue_claim_resend(session, "lead-1", ACCESS_KEY)

    assert 0 < retry_after <= int(RESEND_COOLDOWN.total_seconds())
    assert recent.revoked_at is None
    assert session.added == []
    assert session.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "claimed", "revoked"])
async def test_resend_hides_leads_without_a_paid_unclaimed_link(status):
    session = ResendSession(_lead(status))

    with pytest.raises(HTTPException) as error:
        await queue_claim_resend(session, "lead-1", ACCESS_KEY)

    assert error.value.status_code == 404
    assert session.committed is False


@pytest.mark.asyncio
async def test_resend_hides_another_capability_as_not_found():
    session = ResendSession(_lead())

    with pytest.raises(HTTPException) as error:
        await queue_claim_resend(session, "lead-1", "b" * 32)

    assert error.value.status_code == 404
    assert session.committed is False
