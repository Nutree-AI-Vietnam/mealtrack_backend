"""Mint owner UIDs; never reuse resolve(); never invent a second UID."""

from types import SimpleNamespace

import pytest
from firebase_admin import auth

from src.infra.services import web_funnel_redemption_identity as identity
from src.infra.services.web_funnel_redemption_identity import (
    SILENT_LOGIN_CLAIM,
    RedemptionIdentityUnavailable,
    WebFunnelRedemptionIdentityService,
)


def _record(uid="google-uid", email="buyer@example.com", verified=True, disabled=False):
    return SimpleNamespace(
        uid=uid, email=email, email_verified=verified, disabled=disabled
    )


def _install(monkeypatch, *, get_user=None, create_user=None, by_email=None):
    tokens = []
    updates = []
    created = []
    monkeypatch.setattr(
        identity.auth,
        "get_user",
        get_user or (lambda uid: _record(uid=uid)),
    )
    monkeypatch.setattr(
        identity.auth,
        "create_user",
        create_user
        or (
            lambda **kwargs: created.append(kwargs)
            or _record(uid=kwargs.get("uid") or "new-uid", email=kwargs["email"])
        ),
    )
    monkeypatch.setattr(
        identity.auth,
        "get_user_by_email",
        by_email or (lambda email: _record(email=email)),
    )
    monkeypatch.setattr(
        identity.auth,
        "update_user",
        lambda uid, **kwargs: updates.append((uid, kwargs)),
    )
    monkeypatch.setattr(
        identity.auth,
        "create_custom_token",
        lambda uid, claims: tokens.append((uid, claims)) or b"tok",
    )
    return tokens, updates, created


@pytest.mark.asyncio
async def test_mint_existing_mealtrack_owner_does_not_create_user(monkeypatch):
    tokens, updates, created = _install(monkeypatch)
    token = await WebFunnelRedemptionIdentityService().mint_for_email(
        "buyer@example.com", mealtrack_uid="google-uid"
    )
    assert token == "tok"
    assert created == []
    assert tokens == [("google-uid", {SILENT_LOGIN_CLAIM: 1})]
    assert updates == [
        ("google-uid", {"email": "buyer@example.com", "email_verified": True})
    ]


@pytest.mark.asyncio
async def test_mint_firebase_miss_creates_owner_uid(monkeypatch):
    def get_user(_uid):
        raise auth.UserNotFoundError("missing")

    tokens, _updates, created = _install(monkeypatch, get_user=get_user)
    token = await WebFunnelRedemptionIdentityService().mint_for_email(
        "buyer@example.com", mealtrack_uid="google-uid"
    )
    assert token == "tok"
    assert created == [
        {"uid": "google-uid", "email": "buyer@example.com", "email_verified": True}
    ]
    assert tokens[0][0] == "google-uid"


@pytest.mark.asyncio
async def test_mint_email_already_exists_refetches_owner(monkeypatch):
    def get_user(_uid):
        raise auth.UserNotFoundError("missing")

    def create_user(**_kwargs):
        raise auth.EmailAlreadyExistsError("exists", Exception("x"), None)

    tokens, _updates, _created = _install(
        monkeypatch,
        get_user=get_user,
        create_user=create_user,
        by_email=lambda email: _record(uid="google-uid", email=email),
    )
    token = await WebFunnelRedemptionIdentityService().mint_for_email(
        "buyer@example.com", mealtrack_uid="google-uid"
    )
    assert token == "tok"
    assert tokens[0][0] == "google-uid"


@pytest.mark.asyncio
async def test_mint_refuses_when_email_exists_on_other_uid(monkeypatch):
    def get_user(_uid):
        raise auth.UserNotFoundError("missing")

    def create_user(**_kwargs):
        raise auth.EmailAlreadyExistsError("exists", Exception("x"), None)

    _install(
        monkeypatch,
        get_user=get_user,
        create_user=create_user,
        by_email=lambda email: _record(uid="other-uid", email=email),
    )
    with pytest.raises(RedemptionIdentityUnavailable):
        await WebFunnelRedemptionIdentityService().mint_for_email(
            "buyer@example.com", mealtrack_uid="google-uid"
        )


@pytest.mark.asyncio
async def test_mint_refuses_wf_prefix():
    with pytest.raises(RedemptionIdentityUnavailable):
        await WebFunnelRedemptionIdentityService().mint_for_email(
            "buyer@example.com", mealtrack_uid="wf_abc"
        )


@pytest.mark.asyncio
async def test_mint_refuses_disabled_user(monkeypatch):
    _install(monkeypatch, get_user=lambda _uid: _record(disabled=True))
    with pytest.raises(RedemptionIdentityUnavailable):
        await WebFunnelRedemptionIdentityService().mint_for_email(
            "buyer@example.com", mealtrack_uid="google-uid"
        )


@pytest.mark.asyncio
async def test_mint_verifies_unverified_matching_email(monkeypatch):
    tokens, updates, created = _install(
        monkeypatch, get_user=lambda uid: _record(uid=uid, verified=False)
    )
    token = await WebFunnelRedemptionIdentityService().mint_for_email(
        "buyer@example.com", mealtrack_uid="google-uid"
    )
    assert token == "tok"
    assert created == []
    assert updates[0][1]["email_verified"] is True
