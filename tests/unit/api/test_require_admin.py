"""Hardened admin gate: allowlisted, verified email from a non-custom sign-in."""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth
from starlette.requests import Request

from src.api.dependencies import auth as auth_dep
from src.api.dependencies.auth import require_admin, require_admin_or_local
from src.infra.config.settings import Settings


def _token(email="admin@nutree.ai", *, verified=True, provider="google.com"):
    return {
        "uid": "uid-1",
        "email": email,
        "email_verified": verified,
        "firebase": {"sign_in_provider": provider},
    }


@pytest.fixture(autouse=True)
def _admins(monkeypatch):
    monkeypatch.setattr(
        auth_dep.settings, "ADMIN_EMAILS", " Admin@Nutree.ai ,ops@x.com"
    )


async def _status(token) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(token)
    return exc_info.value.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google.com", "apple.com", "password", None])
async def test_verified_allowlisted_email_is_admin(provider):
    assert await require_admin(_token(provider=provider)) == "admin@nutree.ai"


@pytest.mark.asyncio
async def test_unverified_email_is_forbidden():
    assert await _status(_token(verified=False)) == 403


@pytest.mark.asyncio
async def test_missing_email_verified_claim_is_forbidden():
    token = _token()
    del token["email_verified"]
    assert await _status(token) == 403


@pytest.mark.asyncio
async def test_custom_token_is_forbidden_even_when_verified():
    assert await _status(_token(provider="custom")) == 403


@pytest.mark.asyncio
async def test_email_off_allowlist_is_forbidden():
    assert await _status(_token(email="someone@else.com")) == 403


@pytest.mark.asyncio
async def test_no_email_claim_is_forbidden():
    assert await _status(_token(email=None)) == 403


def _bearer() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="id-token")


@pytest.fixture
def revocation_checks(monkeypatch):
    """Records ``check_revoked`` calls; set ``error`` to make one fail."""
    state = {"calls": [], "error": None}

    async def fake_verify(raw_token, *, check_revoked=False):
        state["calls"].append((raw_token, check_revoked))
        if state["error"] is not None:
            raise state["error"]
        return _token()

    monkeypatch.setattr(auth_dep, "_verify_id_token", fake_verify)
    return state


@pytest.mark.asyncio
async def test_admin_bearer_is_revocation_checked(revocation_checks):
    assert await require_admin(_token(), _bearer()) == "admin@nutree.ai"
    assert revocation_checks["calls"] == [("id-token", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        firebase_auth.RevokedIdTokenError("revoked"),
        firebase_auth.UserDisabledError("disabled"),
    ],
)
async def test_revoked_or_disabled_admin_is_rejected(revocation_checks, error):
    revocation_checks["error"] = error

    with pytest.raises(HTTPException) as exc_info:
        await require_admin(_token(), _bearer())

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_never_triggers_a_revocation_lookup(revocation_checks):
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(_token(email="someone@else.com"), _bearer())

    assert exc_info.value.status_code == 403
    assert revocation_checks["calls"] == []


def _remote_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("203.0.113.9", 1),
        }
    )


@pytest.mark.asyncio
async def test_admin_or_local_applies_hardened_checks(monkeypatch):
    tokens = iter([_token(), _token(provider="custom"), _token(verified=False)])

    async def fake_verify(_request, _credentials):
        return next(tokens)

    monkeypatch.setattr(auth_dep, "verify_firebase_token", fake_verify)

    assert (
        await require_admin_or_local(_remote_request(), credentials=None)
        == "admin@nutree.ai"
    )
    for _ in range(2):
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_or_local(_remote_request(), credentials=None)
        assert exc_info.value.status_code == 403


def test_admin_emails_setting_is_normalized():
    settings = Settings(ADMIN_EMAILS=" A@x.com, ,b@Y.com ")
    assert settings.admin_emails == frozenset({"a@x.com", "b@y.com"})
    assert Settings(ADMIN_EMAILS="").admin_emails == frozenset()
