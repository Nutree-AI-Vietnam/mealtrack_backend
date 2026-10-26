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


def test_custom_firebase_provider_maps_to_email_link():
    assert _auth_provider("custom") is AuthProvider.EMAIL_LINK


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
        verified_provider_app_user_ids={"firebase-user"},
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
        verified_provider_app_user_ids={"$RCAnonymousID:mobile-original"},
    )

    user = next(item for item in session.added if isinstance(item, User))
    assert user.provider is AuthProvider.GOOGLE
    assert user.firebase_uid == "google-user"
    subscription = next(
        item for item in session.added if isinstance(item, Subscription)
    )
    assert subscription.platform == "web"
    assert subscription.expires_at == expires
    assert binding.provider_app_user_ids == sorted(
        {
            "$RCAnonymousID:mobile-original",
            "$RCAnonymousID:web",
            "google-user",
        }
    )
    assert result["access_status"] == "active"
    macros = result["macros"]
    assert macros["calories"] == pytest.approx(
        macros["protein"] * 4 + macros["carbs"] * 4 + macros["fat"] * 9
    )
    assert macros["calories"] != 2000


@pytest.mark.asyncio
async def test_finalization_only_matches_revenuecat_rows_and_keeps_transaction_id():
    from datetime import UTC, datetime, timedelta

    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="google-user",
        product_id="web_monthly",
        environment="SANDBOX",
        verified_app_user_id="$RCAnonymousID:web",
        verified_at=utcnow(),
    )
    lead = _lead()
    lead.snapshot = _snapshot()
    owner = User(
        id="user-1",
        firebase_uid="google-user",
        email="buyer@example.com",
    )
    existing = Subscription(
        user_id="user-1",
        revenuecat_subscriber_id="google-user",
        product_id="web_monthly",
        platform="web",
        status="active",
        provider="revenuecat",
        store_transaction_id="sub_stripe_1",
        purchased_at=datetime.now(UTC) - timedelta(days=1),
    )

    class Session:
        def __init__(self):
            self.results = iter([binding, owner, owner, object(), object(), existing])
            self.statements = []

        async def scalar(self, statement):
            self.statements.append(statement)
            return next(self.results)

        async def get(self, _model, _identifier, **_kwargs):
            return lead

        def add(self, item):
            raise AssertionError(f"unexpected insert of {type(item).__name__}")

        async def commit(self):
            return None

    snapshot_at = datetime.now(UTC)
    session = Session()
    await finalize_redemption(
        session,
        uid="google-user",
        email="buyer@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-existing-row",
        environment="SANDBOX",
        expires_at=snapshot_at + timedelta(days=30),
        provider_snapshot_at=snapshot_at,
        subscription_facts={
            "product_id": "web_monthly",
            "platform": "web",
            "status": "active",
            "purchased_at": snapshot_at - timedelta(days=1),
            "grace_period_expires_at": None,
            "period_type": "normal",
            "cancelled_at": None,
            "is_sandbox": True,
            "store_transaction_id": None,
        },
    )

    subscription_sql = str(
        session.statements[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "subscriptions.provider = 'revenuecat'" in subscription_sql
    assert existing.provider_snapshot_at == snapshot_at
    assert existing.store_transaction_id == "sub_stripe_1"


def _web_facts(now, **overrides):
    """Normalized RevenueCat facts as the finalize route passes them."""
    from datetime import timedelta

    return {
        "product_id": "web_monthly",
        "platform": "web",
        "status": "billing_issue",
        "purchased_at": now - timedelta(days=29),
        "expires_at": now + timedelta(days=1),
        "grace_period_expires_at": now + timedelta(days=6),
        "period_type": "trial",
        "cancelled_at": now - timedelta(hours=2),
        "is_sandbox": True,
        "store_transaction_id": "sub_stripe_9",
    } | overrides


@pytest.mark.asyncio
async def test_finalization_inserts_a_verified_row_with_every_normalized_fact():
    """A new web buyer's row must be verified, or entitlements answer 503."""
    from datetime import UTC, datetime, timedelta

    from src.domain.model.subscription_access import evaluate_subscription_access

    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="google-user",
        product_id="web_monthly",
        environment="SANDBOX",
        verified_app_user_id="$RCAnonymousID:web",
        verified_at=utcnow(),
    )
    lead = _lead()
    lead.snapshot = _snapshot(target_calories=None)

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

    now = datetime.now(UTC)
    facts = _web_facts(now)
    session = Session()
    await finalize_redemption(
        session,
        uid="google-user",
        email="buyer@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-new-row",
        environment="SANDBOX",
        auth_provider="google.com",
        expires_at=facts["expires_at"],
        provider_snapshot_at=now,
        subscription_facts=facts,
    )

    subscription = next(
        item for item in session.added if isinstance(item, Subscription)
    )
    assert subscription.provider == "revenuecat"
    assert subscription.revenuecat_subscriber_id == "google-user"
    assert subscription.provider_snapshot_at == now
    assert subscription.provider_sync_required_at is None
    for column in (
        "product_id",
        "platform",
        "status",
        "purchased_at",
        "expires_at",
        "grace_period_expires_at",
        "period_type",
        "cancelled_at",
        "is_sandbox",
        "store_transaction_id",
    ):
        assert getattr(subscription, column) == facts[column], column
    access = evaluate_subscription_access([subscription], now=now + timedelta(days=3))
    assert (access.has_access, access.status) == (True, "grace_period")


@pytest.mark.asyncio
async def test_finalization_overwrites_stale_facts_on_an_existing_row():
    from datetime import UTC, datetime, timedelta

    binding = _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="google-user",
        product_id="web_monthly",
        environment="SANDBOX",
        verified_app_user_id="$RCAnonymousID:web",
        verified_at=utcnow(),
    )
    lead = _lead()
    lead.snapshot = _snapshot()
    owner = User(id="user-1", firebase_uid="google-user", email="buyer@example.com")
    now = datetime.now(UTC)
    existing = Subscription(
        user_id="user-1",
        revenuecat_subscriber_id="google-user",
        product_id="web_monthly",
        platform="ios",
        status="expired",
        provider="revenuecat",
        purchased_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=35),
        grace_period_expires_at=now - timedelta(days=30),
        period_type="normal",
        cancelled_at=None,
        is_sandbox=False,
        provider_snapshot_at=now - timedelta(days=35),
    )

    class Session:
        def __init__(self):
            self.results = iter([binding, owner, owner, object(), object(), existing])

        async def scalar(self, _statement):
            return next(self.results)

        async def get(self, _model, _identifier, **_kwargs):
            return lead

        def add(self, item):
            raise AssertionError(f"unexpected insert of {type(item).__name__}")

        async def commit(self):
            return None

    facts = _web_facts(now, status="active", grace_period_expires_at=None)
    await finalize_redemption(
        Session(),
        uid="google-user",
        email="buyer@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-stale-row",
        environment="SANDBOX",
        expires_at=facts["expires_at"],
        provider_snapshot_at=now,
        subscription_facts=facts,
    )

    assert existing.provider_snapshot_at == now
    for column in (
        "platform",
        "status",
        "purchased_at",
        "expires_at",
        "grace_period_expires_at",
        "period_type",
        "cancelled_at",
        "is_sandbox",
        "store_transaction_id",
    ):
        assert getattr(existing, column) == facts[column], column


def _session_finalizing_over(binding, lead, owner, row):
    """Finalize session double whose subscription lookup returns ``row``."""

    class Session:
        def __init__(self):
            self.results = iter([binding, owner, owner, object(), object(), row])

        async def scalar(self, _statement):
            return next(self.results)

        async def get(self, _model, _identifier, **_kwargs):
            return lead

        def add(self, item):
            raise AssertionError(f"unexpected insert of {type(item).__name__}")

        async def commit(self):
            return None

    return Session()


def _finalize_binding():
    return _binding(
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        preflight_uid="google-user",
        product_id="web_monthly",
        environment="SANDBOX",
        verified_app_user_id="$RCAnonymousID:web",
        verified_at=utcnow(),
    )


@pytest.mark.asyncio
async def test_finalization_resolves_the_marker_a_failed_webhook_read_left():
    """PURCHASE_REDEEMED could not read RevenueCat and left a placeholder;
    finalize's newer snapshot answers it, so entitlements stop answering 503."""
    from datetime import UTC, datetime, timedelta

    from src.domain.model.subscription_access import evaluate_subscription_access

    binding = _finalize_binding()
    lead = _lead()
    lead.snapshot = _snapshot()
    owner = User(id="user-1", firebase_uid="google-user", email="buyer@example.com")
    event_time = datetime.now(UTC) - timedelta(minutes=1)
    placeholder = Subscription(
        user_id="user-1",
        revenuecat_subscriber_id="google-user",
        product_id="web_monthly",
        platform="web",
        status="expired",
        provider="revenuecat",
        purchased_at=event_time,
        provider_sync_required_at=event_time,
        is_sandbox=True,
    )
    now = datetime.now(UTC)
    facts = _web_facts(now, status="active", grace_period_expires_at=None)

    await finalize_redemption(
        _session_finalizing_over(binding, lead, owner, placeholder),
        uid="google-user",
        email="buyer@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-marked-row",
        environment="SANDBOX",
        expires_at=facts["expires_at"],
        provider_snapshot_at=now,
        subscription_facts=facts,
    )

    assert placeholder.provider_sync_required_at is None
    assert evaluate_subscription_access([placeholder], now).has_access is True


@pytest.mark.asyncio
async def test_finalization_keeps_newer_verified_facts_committed_meanwhile():
    """A refresh or webhook committed a newer snapshot between finalize's
    RevenueCat read and its transaction; finalize must not roll it back."""
    from datetime import UTC, datetime, timedelta

    binding = _finalize_binding()
    lead = _lead()
    lead.snapshot = _snapshot()
    owner = User(id="user-1", firebase_uid="google-user", email="buyer@example.com")
    now = datetime.now(UTC)
    newer_expiry = now + timedelta(days=60)
    row = Subscription(
        user_id="user-1",
        revenuecat_subscriber_id="google-user",
        product_id="web_monthly",
        platform="web",
        status="cancelled",
        provider="revenuecat",
        purchased_at=now - timedelta(days=1),
        expires_at=newer_expiry,
        period_type="normal",
        provider_snapshot_at=now,
        is_sandbox=True,
    )
    older_read = now - timedelta(seconds=5)
    facts = _web_facts(older_read, status="active", grace_period_expires_at=None)

    await finalize_redemption(
        _session_finalizing_over(binding, lead, owner, row),
        uid="google-user",
        email="buyer@example.com",
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key="request-older-read",
        environment="SANDBOX",
        expires_at=facts["expires_at"],
        provider_snapshot_at=older_read,
        subscription_facts=facts,
    )

    assert (row.status, row.expires_at, row.provider_snapshot_at) == (
        "cancelled",
        newer_expiry,
        now,
    )
    assert binding.finalized_uid == "google-user"
