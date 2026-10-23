"""Silent-login mint: new buyers only, no email in body, no preflight bind."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes.v1 import web_funnel_redemption_session as session_route
from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelRedemptionPreflightRequest,
)
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_identity import (
    RedemptionIdentityUnavailable,
    silent_login_uid,
)

NEW_BUYER_UID = silent_login_uid("lead-1")


class SessionDb:
    def __init__(self, binding, lead, owner=None):
        self.binding = binding
        self.lead = lead
        self.owner = owner
        self.committed = False
        self._scalar_n = 0

    async def scalar(self, _statement):
        self._scalar_n += 1
        return self.binding if self._scalar_n == 1 else self.owner

    async def get(self, _model, _id, **_kwargs):
        return self.lead

    async def commit(self):
        self.committed = True


class FakeIdentity:
    def __init__(self):
        self.emails: list[str] = []
        self.uids: list[str] = []

    async def mint_for_new_buyer(self, email, *, uid):
        self.emails.append(email)
        self.uids.append(uid)
        return f"custom-token-{len(self.emails)}"


def _owner(firebase_uid):
    return User(
        firebase_uid=firebase_uid,
        email="Buyer@Example.com",
        username="b",
        password_hash="",
    )


def _lead(status="payment_verified"):
    return WebFunnelLead(
        id="lead-1",
        email="buyer@example.com",
        access_key_hash="k",
        request_id="r",
        snapshot_version="v1",
        snapshot={},
        snapshot_hash="h",
        status=status,
        revision=1,
        access_sync_status="pending",
    )


def _binding(**extra):
    return WebFunnelRedemption(
        lead_id="lead-1",
        environment="sandbox",
        project="p",
        original_app_user_id="$RCAnonymousID:x",
        verified_app_user_id="$RCAnonymousID:x",
        entitlement_id="standard",
        product_id="web_monthly",
        verified_at=session_route.utcnow(),
        redemption_link_hash="a" * 64,
        **extra,
    )


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": (host, 1),
        }
    )


_create_session = session_route.create_redemption_session.__wrapped__


@pytest.mark.asyncio
async def test_session_mints_new_buyer_reserved_uid_and_omits_email():
    binding = _binding()
    identity = FakeIdentity()
    response = await _create_session(
        _request("1.1.1.1"),
        WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
        SessionDb(binding, _lead()),
        identity,
    )
    assert response == {
        "version": "redemption_session_v1",
        "custom_token": "custom-token-1",
    }
    assert "email" not in response
    assert identity.uids == [NEW_BUYER_UID]
    assert binding.silent_login_minted_at is not None
    assert binding.silent_login_generation == 1
    assert binding.preflight_uid is None


@pytest.mark.asyncio
async def test_session_refuses_existing_nutree_account_for_lead_email():
    binding = _binding()
    identity = FakeIdentity()
    db = SessionDb(binding, _lead(), _owner("victim-google-uid"))
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.6"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            db,
            identity,
        )
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"
    assert identity.emails == []
    assert binding.silent_login_minted_at is None
    assert db.committed is False


@pytest.mark.asyncio
async def test_session_retry_remints_after_app_synced_the_new_account():
    binding = _binding()
    identity = FakeIdentity()
    response = await _create_session(
        _request("1.1.1.7"),
        WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
        SessionDb(binding, _lead(), _owner(NEW_BUYER_UID)),
        identity,
    )
    assert response["custom_token"] == "custom-token-1"
    assert identity.uids == [NEW_BUYER_UID]


@pytest.mark.asyncio
async def test_session_retry_remints_when_committed_response_was_lost():
    binding = _binding()
    identity = FakeIdentity()
    first_db = SessionDb(binding, _lead())
    retry_db = SessionDb(binding, _lead())
    request = _request("1.1.1.2")
    payload = WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64)

    # The first commit succeeds but its response is lost before the client reads it.
    await _create_session(request, payload, first_db, identity)
    response = await _create_session(request, payload, retry_db, identity)

    assert first_db.committed is True
    assert retry_db.committed is True
    assert response["custom_token"] == "custom-token-2"
    assert identity.emails == ["buyer@example.com", "buyer@example.com"]
    assert identity.uids == [NEW_BUYER_UID, NEW_BUYER_UID]
    assert binding.silent_login_generation == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"finalized_uid": "done"}, "payment_verified"),
        ({}, "refunded"),
        ({}, "revoked"),
        ({}, "conflict"),
    ],
)
async def test_session_404_identical_for_terminal_and_consumed(kwargs, status):
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.3"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            SessionDb(_binding(**kwargs), _lead(status)),
            FakeIdentity(),
        )
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"


@pytest.mark.asyncio
async def test_session_unknown_hash_404():
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.4"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            SessionDb(None, _lead()),
            FakeIdentity(),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_session_identity_unavailable_404_does_not_consume():

    class Boom:
        async def mint_for_new_buyer(self, _email, *, uid):
            raise RedemptionIdentityUnavailable()

    binding = _binding()
    db = SessionDb(binding, _lead())
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.5"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            db,
            Boom(),
        )
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"
    assert binding.silent_login_minted_at is None
    assert db.committed is False


def test_silent_login_token_allowed_requires_claim():
    token = {"wf_silent_login": 1, "firebase": {"sign_in_provider": "custom"}}
    assert session_route.silent_login_token_allowed("custom", token) is True
    assert session_route.silent_login_token_allowed("custom", {"firebase": {}}) is False
    assert session_route.silent_login_token_allowed("google.com", None) is True
