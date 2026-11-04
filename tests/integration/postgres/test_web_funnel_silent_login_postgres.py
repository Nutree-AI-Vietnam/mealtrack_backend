"""PostgreSQL regression: silent login opens only the redemption's own account.

Real route, identity service, preflight and SQL; only Firebase Admin is faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from firebase_admin import auth
from sqlalchemy import select, text
from starlette.requests import Request

from src.api.routes.v1 import web_funnel
from src.api.routes.v1 import web_funnel_redemption_session as session_route
from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelRedemptionPreflightRequest,
)
from src.app.services.web_funnel_claim_common import utcnow
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_identity import (
    SILENT_LOGIN_CLAIM,
    WebFunnelRedemptionIdentityService,
    silent_login_uid,
)

pytestmark = pytest.mark.integration

LINK_HASH = "b" * 64
_create_session = session_route.create_redemption_session.__wrapped__
_preflight = web_funnel.preflight_revenuecat_redemption.__wrapped__


class FakeFirebase:
    """In-memory Firebase Admin users with the errors the real SDK raises."""

    def __init__(self):
        self.records: dict[str, SimpleNamespace] = {}
        self.updates: list[tuple[str, dict]] = []
        self.tokens: list[tuple[str, dict]] = []

    def add(self, uid, email, *, verified, providers=()):
        self.records[uid] = SimpleNamespace(
            uid=uid,
            email=email,
            email_verified=verified,
            disabled=False,
            provider_data=[SimpleNamespace(provider_id=p) for p in providers],
        )
        return self.records[uid]

    def get_user(self, uid):
        if uid not in self.records:
            raise auth.UserNotFoundError("missing")
        return self.records[uid]

    def get_user_by_email(self, email):
        for record in self.records.values():
            if record.email == email.lower():
                return record
        raise auth.UserNotFoundError("missing")

    def create_user(self, *, uid, email, email_verified):
        if uid in self.records:
            raise auth.UidAlreadyExistsError("uid", Exception("x"), None)
        if any(record.email == email for record in self.records.values()):
            raise auth.EmailAlreadyExistsError("email", Exception("x"), None)
        return self.add(uid, email, verified=email_verified)

    def update_user(self, uid, **kwargs):
        self.updates.append((uid, kwargs))

    def create_custom_token(self, uid, claims):
        self.tokens.append((uid, claims))
        return f"token-for-{uid}".encode()


@pytest.fixture
def firebase(monkeypatch) -> FakeFirebase:
    fake = FakeFirebase()
    for name in (
        "get_user",
        "get_user_by_email",
        "create_user",
        "update_user",
        "create_custom_token",
    ):
        monkeypatch.setattr(auth, name, getattr(fake, name))
    return fake


@pytest_asyncio.fixture
async def db(pg_session):
    await _truncate_web_funnel(pg_session)
    yield pg_session
    await pg_session.rollback()
    await _truncate_web_funnel(pg_session)


async def _truncate_web_funnel(session) -> None:
    await session.execute(
        text("TRUNCATE TABLE web_funnel_leads, users RESTART IDENTITY CASCADE")
    )
    await session.commit()


async def _paid_lead(db, email: str) -> WebFunnelLead:
    lead = WebFunnelLead(
        email=email,
        access_key_hash="k" * 64,
        request_id=f"request-{email}",
        snapshot_version="web_onboarding_snapshot_v1",
        snapshot={},
        snapshot_hash="h" * 64,
        status="payment_verified",
        revision=1,
        access_sync_status="pending",
    )
    db.add(lead)
    await db.flush()
    db.add(
        WebFunnelRedemption(
            lead_id=lead.id,
            provider="revenuecat",
            environment="sandbox",
            project="default",
            original_app_user_id=f"$RCAnonymousID:{lead.id}",
            verified_app_user_id=f"$RCAnonymousID:{lead.id}",
            entitlement_id="standard",
            product_id="web_monthly",
            verified_at=utcnow(),
            redemption_link_hash=LINK_HASH,
        )
    )
    await db.commit()
    return lead


async def _nutree_user(db, firebase_uid: str, email: str) -> None:
    db.add(
        User(
            firebase_uid=firebase_uid,
            email=email,
            username=f"user-{firebase_uid}",
            password_hash="",
        )
    )
    await db.commit()


async def _binding(db) -> WebFunnelRedemption:
    await db.rollback()
    return await db.scalar(
        select(WebFunnelRedemption)
        .where(WebFunnelRedemption.redemption_link_hash == LINK_HASH)
        .execution_options(populate_existing=True)
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("10.0.0.1", 1),
        }
    )


async def _session(db):
    return await _create_session(
        _request(),
        WebFunnelRedemptionPreflightRequest(redemption_link_hash=LINK_HASH),
        db,
        WebFunnelRedemptionIdentityService(),
    )


async def _assert_refused(db, firebase: FakeFirebase, *, minted: int = 0) -> None:
    records, tokens = dict(firebase.records), list(firebase.tokens)
    with pytest.raises(HTTPException) as error:
        await _session(db)
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"
    assert firebase.tokens == tokens
    assert firebase.updates == []
    assert firebase.records == records
    binding = await _binding(db)
    assert binding.silent_login_generation == minted


@pytest.mark.asyncio
async def test_hash_for_existing_nutree_account_mints_nothing(db, firebase):
    # Victim signed up with Google; attacker paid for a lead typed with that email.
    victim = firebase.add("victim-google", "victim@example.com", verified=False)
    await _nutree_user(db, "victim-google", "Victim@Example.com")
    await _paid_lead(db, "victim@example.com")

    await _assert_refused(db, firebase)
    assert victim.email_verified is False


@pytest.mark.asyncio
async def test_hash_for_firebase_only_account_mints_nothing(db, firebase):
    firebase.add(
        "victim-apple", "victim@example.com", verified=True, providers=("apple.com",)
    )
    await _paid_lead(db, "victim@example.com")

    await _assert_refused(db, firebase)


@pytest.mark.asyncio
async def test_hash_stops_minting_once_the_mailbox_owner_signs_in(db, firebase):
    # A buyer typed an email with no account yet; its owner signs in later.
    lead = await _paid_lead(db, "owner@example.com")
    uid = silent_login_uid(lead.id)
    await _session(db)
    await _nutree_user(db, uid, "owner@example.com")
    firebase.records[uid].provider_data = [SimpleNamespace(provider_id="password")]

    await _assert_refused(db, firebase, minted=1)


@pytest.mark.asyncio
async def test_new_buyer_signs_in_silently_and_passes_preflight(db, firebase):
    lead = await _paid_lead(db, "new-buyer@example.com")
    uid = silent_login_uid(lead.id)

    first = await _session(db)
    assert first == {
        "version": "redemption_session_v1",
        "custom_token": f"token-for-{uid}",
    }
    assert firebase.records[uid].email == "new-buyer@example.com"
    assert firebase.records[uid].email_verified is True
    assert firebase.tokens == [(uid, {SILENT_LOGIN_CLAIM: 1})]
    assert firebase.updates == []

    # The app synced the new account, then lost the token: the retry still works.
    await _nutree_user(db, uid, "new-buyer@example.com")
    retry = await _session(db)
    assert retry["custom_token"] == f"token-for-{uid}"

    eligible = await _preflight(
        _request(),
        WebFunnelRedemptionPreflightRequest(redemption_link_hash=LINK_HASH),
        {
            "uid": uid,
            "iat": int(utcnow().timestamp()),
            "firebase": {"sign_in_provider": "custom"},
            SILENT_LOGIN_CLAIM: 1,
        },
        db,
    )
    assert eligible == {"version": "redemption_preflight_v1", "eligible": True}
    binding = await _binding(db)
    assert binding.preflight_uid == uid
    assert binding.silent_login_generation == 2
    assert binding.silent_login_minted_at is not None
