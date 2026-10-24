"""Regression tests for the Firebase user-sync route."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes.v1.users import sync_user_from_firebase
from src.api.schemas.request.user_requests import UserSyncRequest
from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)

UID = "firebase-uid-1"


@pytest.mark.asyncio
async def test_sync_maps_email_uid_collision_to_generic_conflict() -> None:
    event_bus = SimpleNamespace(
        send=AsyncMock(
            side_effect=FirebaseIdentityConflictError("internal identity detail")
        )
    )
    request = UserSyncRequest(
        firebase_uid="new-firebase-uid",
        email="buyer@example.com",
        provider="google",
    )
    http_request = SimpleNamespace(headers={"accept-language": "en-US,en"})

    with pytest.raises(HTTPException) as exc_info:
        await sync_user_from_firebase(
            request=request,
            http_request=http_request,
            token={"uid": "new-firebase-uid"},
            event_bus=event_bus,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Account identity could not be verified"


class FakeMomoPasses:
    def __init__(self, holds_pass=True, error=None):
        self.holds_pass, self.error, self.calls = holds_pass, error, []

    async def activate(self, user_id, verified_email):
        self.calls.append((user_id, verified_email))
        if self.error:
            raise self.error
        return self.holds_pass


def _synced_user_bus():
    now = datetime.now(UTC)
    user = {
        "id": "user-1",
        "firebase_uid": UID,
        "email": "buyer@example.com",
        "username": "buyer",
        "provider": "google",
        "is_active": True,
        "onboarding_completed": True,
        "created_at": now,
        "updated_at": now,
        "has_subscription": False,
    }
    result = {"user": user, "created": False, "updated": True, "message": "ok"}
    return SimpleNamespace(send=AsyncMock(return_value=result))


async def _sync(monkeypatch, passes, token):
    monkeypatch.setattr(
        "src.bootstrap.momo_payments.get_momo_pass_service", lambda: passes
    )
    return await sync_user_from_firebase(
        request=UserSyncRequest(
            firebase_uid=UID, email="buyer@example.com", provider="google"
        ),
        http_request=SimpleNamespace(headers={}),
        token={"uid": UID, "email": "buyer@example.com", **token},
        event_bus=_synced_user_bus(),
    )


def _signed_in_with(provider, **claims):
    return {"firebase": {"sign_in_provider": provider}, **claims}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google.com", "apple.com", "password"])
async def test_sync_activates_web_passes_for_a_verified_provider_email(
    monkeypatch, provider
):
    passes = FakeMomoPasses()

    response = await _sync(
        monkeypatch, passes, _signed_in_with(provider, email_verified=True)
    )

    assert passes.calls == [("user-1", "buyer@example.com")]
    assert response.user.has_subscription is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        _signed_in_with("google.com", email_verified=False),
        _signed_in_with("google.com"),
        _signed_in_with("custom", email_verified=True),
        _signed_in_with("custom", email_verified=True, wf_silent_login=1),
        _signed_in_with("phone", email_verified=True),
        {"email_verified": True},
    ],
    ids=[
        "unverified",
        "no-verified-claim",
        "custom",
        "web-funnel-silent-login",
        "phone",
        "no-provider",
    ],
)
async def test_sync_never_matches_web_passes_on_an_unproven_email(monkeypatch, token):
    passes = FakeMomoPasses(holds_pass=False)

    response = await _sync(monkeypatch, passes, token)

    # In-app passes the user owns still activate; only the email is withheld.
    assert passes.calls == [("user-1", None)]
    assert response.user.has_subscription is False


@pytest.mark.asyncio
async def test_failed_pass_activation_never_breaks_sync(monkeypatch):
    passes = FakeMomoPasses(error=RuntimeError("database is down"))

    response = await _sync(
        monkeypatch, passes, _signed_in_with("google.com", email_verified=True)
    )

    assert passes.calls == [("user-1", "buyer@example.com")]
    assert response.user.firebase_uid == UID
    assert response.user.has_subscription is False
