"""Focused safety tests for authenticated RevenueCat redemption."""

import hashlib
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from src.domain.model.auth import AuthProvider
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_completion import (
    _auth_provider,
    _result,
    finalize_redemption,
    utcnow,
)
from src.infra.services.web_funnel_redemption_service import (
    WebFunnelRedemptionService,
)


class Session:
    def __init__(self, binding, lead):
        self.binding = binding
        self.lead = lead
        self.committed = False

    async def scalar(self, _statement):
        return self.binding

    async def get(self, _model, _identifier, **_kwargs):
        return self.lead

    async def commit(self):
        self.committed = True


class StatementCaptureSession:
    statement = None

    async def scalar(self, statement):
        self.statement = statement
        return None


class FinalizationSession:
    def __init__(self, binding, lead, user, email_owner, owner_profile=None):
        self.binding = binding
        self.lead = lead
        self.scalar_results = iter([binding, user, email_owner, owner_profile])

    async def scalar(self, _statement):
        return next(self.scalar_results)

    async def get(self, _model, _identifier, **_kwargs):
        return self.lead


def _lead(email="buyer@example.com"):
    return WebFunnelLead(id="lead-1", email=email)


def _binding(**values):
    return WebFunnelRedemption(lead_id="lead-1", **values)


def _link_hash(link="rc-example://redeem?token=opaque"):
    return hashlib.sha256(link.encode()).hexdigest()


def _snapshot(**overrides):
    return {
        "gender": "female",
        "height": 168,
        "weight": 62,
        "birth_year": 1995,
        "birth_month": 4,
        "birth_day": 20,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "goal": "recomp",
        "training_level": None,
        "custom_protein_g": None,
        "custom_carbs_g": None,
        "custom_fat_g": None,
    } | overrides


def test_result_derives_age_and_default_calories_from_tdee_macros():
    snapshot = _snapshot()

    result = _result(snapshot, "firebase-user")

    expected_age = (
        date.today().year - 1995 - ((date.today().month, date.today().day) < (4, 20))
    )
    macros = result["macros"]
    assert result["age"] == expected_age
    assert macros["calories"] == pytest.approx(
        macros["protein"] * 4 + macros["carbs"] * 4 + macros["fat"] * 9
    )
    assert macros["calories"] != 2000


def test_result_preserves_complete_custom_macro_override():
    result = _result(
        _snapshot(custom_protein_g=125, custom_carbs_g=210, custom_fat_g=70),
        "firebase-user",
    )

    assert result["macros"] == {
        "protein": 125.0,
        "carbs": 210.0,
        "fat": 70.0,
        "calories": 1970.0,
    }


def test_password_firebase_provider_maps_to_email_link():
    assert _auth_provider("password") is AuthProvider.EMAIL_LINK


@pytest.mark.asyncio
async def test_preflight_binds_matching_verified_identity():
    binding = _binding()
    session = Session(binding, _lead())

    assert await WebFunnelRedemptionService().preflight(
        session,
        uid="firebase-uid",
        email="buyer@example.com",
        redemption_link_hash=_link_hash(),
    )
    assert binding.preflight_uid == "firebase-uid"
    assert binding.preflight_at is not None
    assert session.committed


@pytest.mark.asyncio
async def test_preflight_rejects_unknown_link_or_checkout_email_mismatch():
    service = WebFunnelRedemptionService()

    assert not await service.preflight(
        Session(None, _lead()),
        uid="firebase-uid",
        email="buyer@example.com",
        redemption_link_hash=_link_hash("rc-example://unknown"),
    )
    assert not await service.preflight(
        Session(_binding(), _lead()),
        uid="firebase-uid",
        email="other@example.com",
        redemption_link_hash=_link_hash(),
    )


@pytest.mark.asyncio
async def test_preflight_rejects_replayed_redemption_binding():
    assert not await WebFunnelRedemptionService().preflight(
        Session(
            _binding(redeemer_uid="firebase-uid", finalized_uid="firebase-uid"),
            _lead(),
        ),
        uid="firebase-uid",
        email="buyer@example.com",
        redemption_link_hash=_link_hash(),
    )


@pytest.mark.asyncio
async def test_webhook_records_provider_aliases_without_binding_firebase_identity():
    binding = _binding(original_app_user_id="$RCAnonymousID:web")
    session = Session(binding, _lead())

    assert await WebFunnelRedemptionService().record_webhook_redemption(
        session,
        {
            "type": "PURCHASE_REDEEMED",
            "environment": "SANDBOX",
            "redeemed_from": ["$RCAnonymousID:web", "rc-app-user"],
            "redeemed_by": ["$RCAnonymousID:web", "rc-app-user"],
        },
    )
    assert binding.provider_app_user_ids == ["$RCAnonymousID:web", "rc-app-user"]
    assert binding.redeemer_uid is None
    assert binding.redemption_confirmed_at is not None


@pytest.mark.asyncio
async def test_webhook_preserves_existing_firebase_binding_and_unions_aliases():
    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        provider_app_user_ids=["old-alias"],
        redeemer_uid="firebase-uid",
    )
    session = Session(binding, _lead())

    assert await WebFunnelRedemptionService().record_webhook_redemption(
        session,
        {
            "type": "PURCHASE_REDEEMED",
            "environment": "SANDBOX",
            "redeemed_from": ["$RCAnonymousID:web"],
            "redeemed_by": ["$RCAnonymousID:web", "new-alias"],
        },
    )
    assert binding.provider_app_user_ids == [
        "$RCAnonymousID:web",
        "new-alias",
        "old-alias",
    ]
    assert binding.redeemer_uid == "firebase-uid"


@pytest.mark.asyncio
async def test_finalization_selects_by_redemption_link_hash_and_preflight_uid():
    """Ship-first: exact row via hash+UID — never latest alias/verified_at."""
    session = StatementCaptureSession()

    with pytest.raises(HTTPException):
        await finalize_redemption(
            session,
            uid="firebase-uid",
            email="buyer@example.com",
            original_app_user_id="$RCAnonymousID:web",
            redemption_link_hash=_link_hash(),
            idempotency_key="request-hash-select",
            environment="SANDBOX",
        )

    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "redemption_link_hash" in sql
    assert "preflight_uid" in sql
    assert "ORDER BY" not in sql.upper()


@pytest.mark.asyncio
async def test_finalization_rejects_expired_provider_entitlement():
    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="firebase-uid",
        environment="SANDBOX",
        product_id="web_monthly",
        verified_app_user_id="$RCAnonymousID:web",
    )
    from datetime import UTC, datetime, timedelta

    with pytest.raises(HTTPException) as error:
        await finalize_redemption(
            FinalizationSession(binding, _lead(), None, None),
            uid="firebase-uid",
            email="buyer@example.com",
            original_app_user_id="$RCAnonymousID:web",
            redemption_link_hash=_link_hash(),
            idempotency_key="request-expired",
            environment="SANDBOX",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_finalization_rejects_user_different_from_legacy_preflight_binding():
    # Hash+UID WHERE clause excludes mismatched preflight_uid (mock returns None).
    with pytest.raises(HTTPException) as error:
        await finalize_redemption(
            StatementCaptureSession(),
            uid="different-user",
            email="buyer@example.com",
            original_app_user_id="$RCAnonymousID:web",
            redemption_link_hash=_link_hash(),
            idempotency_key="request-preflight-binding",
            environment="SANDBOX",
        )

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_finalization_accepts_preflight_bound_provider_identity_before_webhook():
    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="firebase-user",
        environment="SANDBOX",
        finalized_uid="firebase-user",
    )

    result = await finalize_redemption(
        FinalizationSession(binding, _lead(), None, None),
        uid="firebase-user",
        email="buyer@example.com",
        original_app_user_id="firebase-user",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-provider-before-webhook",
        environment="SANDBOX",
    )

    assert result == {
        "version": "redemption_result_v1",
        "access_status": "active",
    }


@pytest.mark.asyncio
async def test_finalization_rejects_new_link_without_preflight_binding():
    with pytest.raises(HTTPException) as error:
        await finalize_redemption(
            StatementCaptureSession(),
            uid="firebase-user",
            email="buyer@example.com",
            original_app_user_id="$RCAnonymousID:web",
            redemption_link_hash=_link_hash(),
            idempotency_key="request-missing-preflight",
            environment="SANDBOX",
        )

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_finalization_identifies_existing_account_recovery():
    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="anonymous-user",
        verified_app_user_id="$RCAnonymousID:web",
        environment="SANDBOX",
    )
    owner = User(
        firebase_uid="existing-user",
        email="buyer@example.com",
        onboarding_completed=False,
    )

    with pytest.raises(HTTPException) as error:
        await finalize_redemption(
            FinalizationSession(binding, _lead(), None, owner),
            uid="anonymous-user",
            email="buyer@example.com",
            original_app_user_id="$RCAnonymousID:web",
            redemption_link_hash=_link_hash(),
            idempotency_key="request-existing-account",
            environment="SANDBOX",
        )

    assert error.value.status_code == 409
    assert error.value.detail == {
        "code": "EXISTING_ACCOUNT_REQUIRES_SIGN_IN",
        "message": "Sign in to the existing Nutree account to continue.",
    }


@pytest.mark.asyncio
async def test_finalization_attaches_purchase_to_authenticated_user():
    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="google-user",
        product_id="web_monthly",
        environment="SANDBOX",
        provider="revenuecat",
        project="default",
        verified_app_user_id="$RCAnonymousID:web",
        entitlement_id="standard",
        verified_at=utcnow(),
    )
    lead = _lead()
    lead.snapshot = {
        "gender": "female",
        "height": 168,
        "weight": 62,
        "birth_year": 1995,
        "birth_month": 4,
        "birth_day": 20,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "goal": "recomp",
        "target_calories": None,
        "custom_protein_g": None,
        "custom_carbs_g": None,
        "custom_fat_g": None,
    }

    class Session:
        def __init__(self):
            self.scalars = iter([binding, None, None, None, None, None])
            self.added = []

        async def scalar(self, _statement):
            return next(self.scalars)

        async def get(self, _model, _identifier, **_kwargs):
            return lead

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            for item in self.added:
                if isinstance(item, User):
                    item.id = "user-1"

        async def commit(self):
            return None

    from datetime import UTC, datetime, timedelta

    expires = datetime.now(UTC) + timedelta(days=30)
    session = Session()
    result = await finalize_redemption(
        session,
        uid="google-user",
        email="BUYER@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-google-user",
        environment="SANDBOX",
        auth_provider="google.com",
        expires_at=expires,
    )

    user = next(item for item in session.added if isinstance(item, User))
    assert user.provider is AuthProvider.GOOGLE
    assert user.firebase_uid == "google-user"
    subscription = next(
        item for item in session.added if isinstance(item, Subscription)
    )
    assert subscription.platform == "web"
    assert subscription.expires_at == expires
    assert result["access_status"] == "active"
    macros = result["macros"]
    assert macros["calories"] == pytest.approx(
        macros["protein"] * 4 + macros["carbs"] * 4 + macros["fat"] * 9
    )
    assert macros["calories"] != 2000
