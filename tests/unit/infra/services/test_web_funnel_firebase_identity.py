"""Legacy resolve() must keep refusing Google/Apple identities."""

from types import SimpleNamespace

import pytest
from firebase_admin import auth

from src.infra.services.web_funnel_firebase_identity import (
    FirebaseIdentityConflict,
    WebFunnelFirebaseIdentityService,
)


def _record(*, provider_id: str, email: str = "buyer@example.com"):
    return SimpleNamespace(
        uid="existing-uid",
        email=email,
        email_verified=True,
        disabled=False,
        provider_data=[SimpleNamespace(provider_id=provider_id)],
    )


@pytest.mark.asyncio
async def test_resolve_rejects_google_provider(monkeypatch):
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: _record(provider_id="google.com"))
    with pytest.raises(FirebaseIdentityConflict):
        await WebFunnelFirebaseIdentityService().resolve("lead-1", "buyer@example.com")


@pytest.mark.asyncio
async def test_resolve_rejects_apple_provider(monkeypatch):
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: _record(provider_id="apple.com"))
    with pytest.raises(FirebaseIdentityConflict):
        await WebFunnelFirebaseIdentityService().resolve("lead-1", "buyer@example.com")
