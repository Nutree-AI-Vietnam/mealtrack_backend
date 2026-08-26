"""Possession and safe-projection tests for the dark web-funnel lead API."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import Response

from src.api.routes.v1 import web_funnel
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)


class FakeSession:
    def __init__(self, lead: WebFunnelLead | None):
        self.lead = lead
        self.committed = False

    async def get(self, _model, _identifier, **_kwargs):
        return self.lead

    async def commit(self):
        self.committed = True


class CreateSession(FakeSession):
    def __init__(self, existing: WebFunnelLead | None, *, conflict: bool = False):
        super().__init__(existing)
        self.conflict = conflict
        self.added: list[object] = []
        self.scalar_calls = 0

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return self.lead

    def add(self, item):
        self.added.append(item)

    async def refresh(self, _item):
        return None

    async def commit(self):
        if self.conflict and self.scalar_calls == 1:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        self.committed = True

    async def rollback(self):
        return None


class ConcurrentCreateSession(CreateSession):
    def __init__(self, winner: WebFunnelLead):
        super().__init__(None, conflict=True)
        self.winner = winner

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return self.winner if self.scalar_calls > 1 else None


class CorrelationSession(FakeSession):
    def __init__(self, lead: WebFunnelLead, existing=None):
        super().__init__(lead)
        self.existing = existing
        self.added: list[object] = []

    async def scalar(self, _statement):
        return self.existing

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None


def _lead() -> WebFunnelLead:
    return WebFunnelLead(
        id="lead-1",
        email="buyer@example.com",
        access_key_hash=web_funnel._hash("a" * 32),
        request_id="request-1",
        snapshot_version="web_onboarding_snapshot_v1",
        snapshot={},
        snapshot_hash="snapshot",
        status="draft",
        revision=1,
        access_sync_status="pending",
    )


def _payload():
    return web_funnel.WebFunnelLeadCreateRequest.model_validate(
        {
            "email": "buyer@example.com",
            "payload": {
                "birth_year": 1995,
                "birth_month": 4,
                "birth_day": 20,
                "gender": "female",
                "height": 168,
                "weight": 62,
                "job_type": "desk",
                "training_days_per_week": 3,
                "training_minutes_per_session": 45,
                "goal": "recomp",
            },
        }
    )


def _request(client_host: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": (client_host, 1),
        }
    )


@pytest.mark.asyncio
async def test_status_is_possession_bound_and_never_returns_email_or_snapshot():
    response = await web_funnel.get_lead_status(
        _request(), "lead-1", "a" * 32, FakeSession(_lead())
    )
    assert response == {
        "lead_id": "lead-1",
        "masked_email": "b***@example.com",
        "status": "draft",
    }


@pytest.mark.asyncio
async def test_status_hides_wrong_capability_as_not_found():
    with pytest.raises(HTTPException) as error:
        await web_funnel.get_lead_status(
            _request(), "lead-1", "b" * 32, FakeSession(_lead())
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_reset_revokes_only_unpaid_possession_bound_draft():
    session = FakeSession(_lead())
    assert await web_funnel.reset_lead(_request(), "lead-1", "a" * 32, session) == {
        "status": "revoked"
    }
    assert session.lead.status == "revoked"
    assert session.lead.revoked_at is not None
    assert session.committed


@pytest.mark.asyncio
async def test_create_replays_same_request_only_to_same_capability(monkeypatch):
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_ORIGIN", "https://web.example"
    )
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_SHARED_SECRET", "bff-secret"
    )
    response = await web_funnel.create_lead(
        _request(),
        _payload(),
        "a" * 32,
        "request-1",
        "https://web.example",
        "bff-secret",
        CreateSession(_lead()),
    )
    assert response["lead_id"] == "lead-1"


@pytest.mark.asyncio
async def test_create_hides_replayed_request_from_another_capability(monkeypatch):
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_ORIGIN", "https://web.example"
    )
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_SHARED_SECRET", "bff-secret"
    )
    with pytest.raises(HTTPException) as error:
        await web_funnel.create_lead(
            _request(),
            _payload(),
            "b" * 32,
            "request-1",
            "https://web.example",
            "bff-secret",
            CreateSession(_lead()),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_create_requires_server_held_bff_credential(monkeypatch):
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_ORIGIN", "https://web.example"
    )
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_SHARED_SECRET", "bff-secret"
    )
    with pytest.raises(HTTPException) as error:
        await web_funnel.create_lead(
            _request(),
            _payload(),
            "a" * 32,
            "request-1",
            "https://web.example",
            "attacker-token",
            CreateSession(None),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_create_concurrent_unique_conflict_converges_to_winner(monkeypatch):
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_ORIGIN", "https://web.example"
    )
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_SHARED_SECRET", "bff-secret"
    )
    response = await web_funnel.create_lead(
        _request(),
        _payload(),
        "a" * 32,
        "request-1",
        "https://web.example",
        "bff-secret",
        ConcurrentCreateSession(_lead()),
    )
    assert response["lead_id"] == "lead-1"


def _configure_redemption(monkeypatch):
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_ORIGIN", "https://web.example"
    )
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_BFF_SHARED_SECRET", "bff-secret"
    )
    monkeypatch.setattr(web_funnel.settings, "WEB_FUNNEL_REDEMPTION_ENABLED", True)
    monkeypatch.setattr(
        web_funnel.settings, "WEB_FUNNEL_REVENUECAT_ENVIRONMENT", "sandbox"
    )
    monkeypatch.setattr(web_funnel.settings, "REVENUECAT_SECRET_API_KEY", "secret")


class VerifiedSubscriberService:
    async def get_subscriber_info(self, _app_user_id):
        return {
            "subscriber": {
                "original_app_user_id": "$RCAnonymousID:customer",
                "entitlements": {
                    "standard": {
                        "product_identifier": "web_monthly",
                        "expires_date": None,
                    }
                },
            }
        }


def test_correlation_requires_redemption_link_hash():
    with pytest.raises(ValueError):
        web_funnel.WebFunnelRevenueCatCorrelationRequest(
            app_user_id="$RCAnonymousID:customer"
        )


@pytest.mark.asyncio
async def test_redemption_finalization_uses_provider_derived_customer_and_fresh_verified_identity(
    monkeypatch,
):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    captured = {}

    class RedemptionService:
        async def finalize(self, _db, **kwargs):
            captured.update(kwargs)
            return {"version": "redemption_result_v1", "access_status": "active"}

    monkeypatch.setattr(
        web_funnel, "get_web_funnel_redemption_service", lambda: RedemptionService()
    )
    token = {
        "uid": "firebase-uid",
        "email": "buyer@example.com",
        "email_verified": True,
        "iat": int(web_funnel.utcnow().timestamp()),
        "firebase": {"sign_in_provider": "google.com"},
    }
    response = await web_funnel.finalize_revenuecat_redemption(
        _request(),
        web_funnel.WebFunnelRedemptionFinalizeRequest(
            confirm_apply_purchase=True,
            redemption_link_hash="a" * 64,
        ),
        Response(),
        "x" * 16,
        token,
        object(),
    )

    assert response["version"] == "redemption_result_v1"
    assert captured == {
        "uid": "firebase-uid",
        "email": "buyer@example.com",
        "original_app_user_id": "$RCAnonymousID:customer",
        "redemption_link_hash": "a" * 64,
        "idempotency_key": "x" * 16,
        "environment": "sandbox",
        "auth_provider": "google.com",
        "expires_at": None,
    }


@pytest.mark.asyncio
async def test_redemption_finalization_rejects_anonymous_identity(monkeypatch):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    captured = {}

    class RedemptionService:
        async def finalize(self, _db, **kwargs):
            captured.update(kwargs)
            return {"version": "redemption_result_v1", "access_status": "active"}

    monkeypatch.setattr(
        web_funnel, "get_web_funnel_redemption_service", lambda: RedemptionService()
    )
    token = {
        "uid": "firebase-uid",
        "iat": int(web_funnel.utcnow().timestamp()),
        "firebase": {"sign_in_provider": "anonymous"},
    }
    with pytest.raises(HTTPException) as error:
        await web_funnel.finalize_revenuecat_redemption(
            _request("127.0.0.2"),
            web_funnel.WebFunnelRedemptionFinalizeRequest(
                confirm_apply_purchase=True,
                redemption_link_hash="a" * 64,
            ),
            Response(),
            "x" * 16,
            token,
            object(),
        )

    assert error.value.status_code == 403
    assert captured == {}


@pytest.mark.asyncio
async def test_redemption_finalization_accepts_passwordless_email_identity(
    monkeypatch,
):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    captured = {}

    class RedemptionService:
        async def finalize(self, _db, **kwargs):
            captured.update(kwargs)
            return {"version": "redemption_result_v1", "access_status": "active"}

    monkeypatch.setattr(
        web_funnel, "get_web_funnel_redemption_service", lambda: RedemptionService()
    )
    token = {
        "uid": "firebase-uid",
        "email": "buyer@example.com",
        "email_verified": True,
        "iat": int(web_funnel.utcnow().timestamp()),
        "firebase": {"sign_in_provider": "password"},
    }

    response = await web_funnel.finalize_revenuecat_redemption(
        _request("127.0.0.3"),
        web_funnel.WebFunnelRedemptionFinalizeRequest(
            confirm_apply_purchase=True,
            redemption_link_hash="a" * 64,
        ),
        Response(),
        "x" * 16,
        token,
        object(),
    )

    assert response["access_status"] == "active"
    assert captured["auth_provider"] == "password"


@pytest.mark.asyncio
async def test_correlation_binds_verified_anonymous_web_customer(monkeypatch):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    session = CorrelationSession(_lead())
    payload = web_funnel.WebFunnelRevenueCatCorrelationRequest(
        app_user_id="$RCAnonymousID:customer",
        redemption_link_hash=web_funnel._hash("rc-example://redeem?token=opaque"),
    )

    response = await web_funnel.correlate_revenuecat_customer(
        _request(),
        "lead-1",
        payload,
        "a" * 32,
        "https://web.example",
        "bff-secret",
        session,
    )

    assert response["status"] == "payment_verified"
    assert "preflight_token" not in response
    assert session.committed
    binding = session.added[0]
    assert binding.original_app_user_id == "$RCAnonymousID:customer"
    assert binding.verified_app_user_id == "$RCAnonymousID:customer"
    assert binding.entitlement_id == "standard"
    assert binding.project == "default"
    assert binding.redemption_link_hash == web_funnel._hash(
        "rc-example://redeem?token=opaque"
    )


@pytest.mark.asyncio
async def test_correlation_accepts_existing_binding_with_legacy_project_label(
    monkeypatch,
):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    existing = WebFunnelRedemption(
        lead_id="lead-1",
        provider="revenuecat",
        environment="sandbox",
        project="nutree",
        original_app_user_id="$RCAnonymousID:customer",
        verified_app_user_id="$RCAnonymousID:customer",
        entitlement_id="standard",
        product_id="web_monthly",
        verified_at=web_funnel.utcnow(),
    )
    session = CorrelationSession(_lead(), existing=existing)

    response = await web_funnel.correlate_revenuecat_customer(
        _request("127.0.0.2"),
        "lead-1",
        web_funnel.WebFunnelRevenueCatCorrelationRequest(
            app_user_id="$RCAnonymousID:customer",
            redemption_link_hash=web_funnel._hash("rc-example://redeem?token=opaque"),
        ),
        "a" * 32,
        "https://web.example",
        "bff-secret",
        session,
    )

    assert response["status"] == "payment_verified"
    assert not session.added


@pytest.mark.asyncio
async def test_correlation_hides_unverified_customer_as_not_found(monkeypatch):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    session = CorrelationSession(_lead())
    payload = web_funnel.WebFunnelRevenueCatCorrelationRequest(
        app_user_id="$RCAnonymousID:other",
        redemption_link_hash=web_funnel._hash("rc-example://redeem?token=opaque"),
    )

    with pytest.raises(HTTPException) as error:
        await web_funnel.correlate_revenuecat_customer(
            _request(),
            "lead-1",
            payload,
            "a" * 32,
            "https://web.example",
            "bff-secret",
            session,
        )

    assert error.value.status_code == 404
    assert not session.added


@pytest.mark.asyncio
async def test_correlation_blocks_new_checkout_when_admission_disabled(
    monkeypatch,
):
    _configure_redemption(monkeypatch)
    monkeypatch.setattr(web_funnel.settings, "WEB_FUNNEL_CHECKOUT_ADMISSION_ENABLED", False)
    monkeypatch.setattr(
        web_funnel,
        "_get_web_funnel_subscription_service",
        lambda: VerifiedSubscriberService(),
    )
    session = CorrelationSession(_lead())
    payload = web_funnel.WebFunnelRevenueCatCorrelationRequest(
        app_user_id="$RCAnonymousID:customer",
        redemption_link_hash=web_funnel._hash("rc-example://redeem?token=opaque"),
    )

    with pytest.raises(HTTPException) as error:
        await web_funnel.correlate_revenuecat_customer(
            _request(),
            "lead-1",
            payload,
            "a" * 32,
            "https://web.example",
            "bff-secret",
            session,
        )

    assert error.value.status_code == 404
    assert not session.added
