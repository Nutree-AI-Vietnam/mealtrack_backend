"""Silent-login mint: owner UID, no email in body, no preflight bind."""

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
)


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
    def __init__(self, uid="google-uid"):
        self.uid = uid
        self.emails: list[str] = []

    async def mint_for_email(self, email, *, mealtrack_uid):
        self.emails.append(email)
        self.mealtrack_uid = mealtrack_uid
        return "custom-token"


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


def _enable(monkeypatch):
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_REDEMPTION_ENABLED", True)
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_SILENT_LOGIN_ENABLED", True)


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
async def test_session_mints_for_mealtrack_owner_and_omits_email(monkeypatch):
    _enable(monkeypatch)
    owner = User(firebase_uid="google-uid", email="buyer@example.com", username="b", password_hash="")
    binding = _binding()
    identity = FakeIdentity()
    response = await _create_session(
        _request("1.1.1.1"),
        WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
        SessionDb(binding, _lead(), owner),
        identity,
    )
    assert response == {"version": "redemption_session_v1", "custom_token": "custom-token"}
    assert "email" not in response
    assert identity.mealtrack_uid == "google-uid"
    assert binding.silent_login_minted_at is not None
    assert binding.silent_login_generation == 1
    assert binding.preflight_uid is None


@pytest.mark.asyncio
async def test_session_404_when_flag_off(monkeypatch):
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_REDEMPTION_ENABLED", True)
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_SILENT_LOGIN_ENABLED", False)
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.2"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            SessionDb(_binding(), _lead()),
            FakeIdentity(),
        )
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"finalized_uid": "done"}, "payment_verified"),
        ({"silent_login_minted_at": session_route.utcnow()}, "payment_verified"),
        ({}, "refunded"),
        ({}, "revoked"),
        ({}, "conflict"),
    ],
)
async def test_session_404_identical_for_terminal_and_consumed(monkeypatch, kwargs, status):
    _enable(monkeypatch)
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
async def test_session_unknown_hash_404(monkeypatch):
    _enable(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await _create_session(
            _request("1.1.1.4"),
            WebFunnelRedemptionPreflightRequest(redemption_link_hash="a" * 64),
            SessionDb(None, _lead()),
            FakeIdentity(),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_session_identity_unavailable_404_does_not_consume(monkeypatch):
    _enable(monkeypatch)

    class Boom:
        async def mint_for_email(self, _email, *, mealtrack_uid):
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


def test_silent_login_token_allowed_requires_claim_when_flag_on(monkeypatch):
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_SILENT_LOGIN_ENABLED", True)
    token = {"wf_silent_login": 1, "firebase": {"sign_in_provider": "custom"}}
    assert session_route.silent_login_token_allowed("custom", token) is True
    assert session_route.silent_login_token_allowed("custom", {"firebase": {}}) is False
    monkeypatch.setattr(session_route.settings, "WEB_FUNNEL_SILENT_LOGIN_ENABLED", False)
    assert session_route.silent_login_token_allowed("custom", token) is False
    assert session_route.silent_login_token_allowed("google.com", None) is True
