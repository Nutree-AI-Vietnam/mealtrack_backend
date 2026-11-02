"""Mint only the redemption's own UID; never adopt or verify an existing account."""

from types import SimpleNamespace

import pytest
from firebase_admin import auth

from src.infra.services import web_funnel_redemption_identity as identity
from src.infra.services.web_funnel_firebase_identity import (
    WebFunnelFirebaseIdentityService,
)
from src.infra.services.web_funnel_redemption_identity import (
    SILENT_LOGIN_CLAIM,
    RedemptionIdentityUnavailable,
    WebFunnelRedemptionIdentityService,
    silent_login_uid,
)

UID = silent_login_uid("lead-1")


def _record(uid=UID, email="buyer@example.com", disabled=False, providers=()):
    return SimpleNamespace(
        uid=uid,
        email=email,
        email_verified=True,
        disabled=disabled,
        provider_data=[SimpleNamespace(provider_id=p) for p in providers],
    )


def _missing(_uid):
    raise auth.UserNotFoundError("missing")


def _email_taken(**_kwargs):
    raise auth.EmailAlreadyExistsError("exists", Exception("x"), None)


def _install(monkeypatch, *, get_user=_missing, create_user=None):
    tokens = []
    created = []

    def never(*_args, **_kwargs):
        raise AssertionError("silent login must not read or update other records")

    monkeypatch.setattr(identity.auth, "get_user", get_user)
    monkeypatch.setattr(
        identity.auth,
        "create_user",
        create_user
        or (
            lambda **kwargs: (
                created.append(kwargs)
                or _record(uid=kwargs["uid"], email=kwargs["email"])
            )
        ),
    )
    monkeypatch.setattr(identity.auth, "get_user_by_email", never)
    monkeypatch.setattr(identity.auth, "update_user", never)
    monkeypatch.setattr(
        identity.auth,
        "create_custom_token",
        lambda uid, claims: tokens.append((uid, claims)) or b"tok",
    )
    return tokens, created


async def _mint(email="buyer@example.com"):
    return await WebFunnelRedemptionIdentityService().mint_for_new_buyer(email, uid=UID)


def test_silent_login_uid_is_stable_per_lead_and_outside_legacy_prefix():
    assert silent_login_uid("lead-1") == UID
    assert silent_login_uid("lead-2") != UID
    assert UID.startswith("wfr_") and not UID.startswith("wf_")
    assert UID != WebFunnelFirebaseIdentityService._uid_for("lead-1")


@pytest.mark.asyncio
async def test_new_buyer_gets_a_record_under_the_reserved_uid(monkeypatch):
    tokens, created = _install(monkeypatch)
    assert await _mint(" Buyer@Example.com ") == "tok"
    assert created == [
        {"uid": UID, "email": "buyer@example.com", "email_verified": True}
    ]
    assert tokens == [(UID, {SILENT_LOGIN_CLAIM: 1})]


@pytest.mark.asyncio
async def test_retry_remints_the_record_this_redemption_created(monkeypatch):
    tokens, created = _install(monkeypatch, get_user=lambda uid: _record(uid=uid))
    assert await _mint() == "tok"
    assert created == []
    assert tokens == [(UID, {SILENT_LOGIN_CLAIM: 1})]


@pytest.mark.asyncio
async def test_uid_race_rereads_the_record(monkeypatch):
    reads = iter([None, _record()])

    def get_user(_uid):
        record = next(reads)
        if record is None:
            raise auth.UserNotFoundError("missing")
        return record

    def create_user(**_kwargs):
        raise auth.UidAlreadyExistsError("exists", Exception("x"), None)

    tokens, _created = _install(monkeypatch, get_user=get_user, create_user=create_user)
    assert await _mint() == "tok"
    assert tokens[0][0] == UID


@pytest.mark.asyncio
async def test_existing_firebase_account_for_the_email_is_refused(monkeypatch):
    tokens, _created = _install(monkeypatch, create_user=_email_taken)
    with pytest.raises(RedemptionIdentityUnavailable):
        await _mint()
    assert tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        _record(providers=("google.com",)),
        _record(providers=("apple.com",)),
        # Email-link sign-in by the mailbox owner shows up as "password".
        _record(providers=("password",)),
        _record(disabled=True),
        _record(email="someone-else@example.com"),
        _record(email=None),
    ],
    ids=[
        "google-linked",
        "apple-linked",
        "email-link-linked",
        "disabled",
        "email-changed",
        "no-email",
    ],
)
async def test_reserved_record_claimed_or_changed_is_refused(monkeypatch, record):
    tokens, _created = _install(monkeypatch, get_user=lambda _uid: record)
    with pytest.raises(RedemptionIdentityUnavailable):
        await _mint()
    assert tokens == []
