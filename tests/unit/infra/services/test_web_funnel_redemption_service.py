"""Focused safety tests for authenticated RevenueCat redemption."""

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from tests.fixtures.revenuecat_samples import standard_annual_response

from src.domain.model.auth import AuthProvider
from src.domain.ports.subscription_service_port import RevenueCatSnapshot
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


def _web_snapshot(
    read_at,
    *,
    expires_in=timedelta(days=30),
    app_user_id="google-user",
    store_transaction_id="sub_stripe_9",
    **subscription_fields,
):
    """A verified RevenueCat Web customer whose standard entitlement is the
    bound web_monthly product, as finalize's caller reads it."""
    body = standard_annual_response(
        request_date=read_at,
        expires_date=read_at + expires_in,
        store="stripe",
        store_transaction_id=store_transaction_id,
    )
    subscriber = body["subscriber"]
    web = subscriber["subscriptions"].pop("annual")
    web.update(is_sandbox=True, **subscription_fields)
    subscriber["subscriptions"] = {"web_monthly": web}
    subscriber["entitlements"]["standard"]["product_identifier"] = "web_monthly"
    return RevenueCatSnapshot(subscriber, read_at, app_user_id=app_user_id)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FinalizeSession:
    """Answers finalize's own reads in order (binding, user by Firebase uid,
    email owner, profile, weekly budget), then the access sync's: the user
    lock and the user's RevenueCat rows."""

    def __init__(self, binding, lead, user=None, *, profile=None, budget=None, rows=()):
        self.lead = lead
        self.user = user
        self.rows = list(rows)
        self.added = []
        self.statements = []
        self.committed = False
        self._reads = iter([binding, user, user, profile, budget])

    async def scalar(self, statement):
        self.statements.append(statement)
        return next(self._reads, self.user)

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Rows(row for row in self.rows if row.provider == "revenuecat")

    async def get(self, _model, _identifier, **_kwargs):
        return self.lead

    def add(self, item):
        self.added.append(item)
        if isinstance(item, User):
            self.user = item
        if isinstance(item, Subscription):
            self.rows.append(item)

    async def flush(self):
        if self.user is not None and self.user.id is None:
            self.user.id = "user-1"

    async def commit(self):
        self.committed = True


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


def _owner():
    return User(id="user-1", firebase_uid="google-user", email="buyer@example.com")


def _web_row(**values):
    return Subscription(
        **{
            "id": "web-row",
            "user_id": "user-1",
            "revenuecat_subscriber_id": "google-user",
            "provider": "revenuecat",
            "product_id": "web_monthly",
            "platform": "web",
            "status": "active",
            "purchased_at": utcnow() - timedelta(days=1),
            "is_sandbox": True,
        }
        | values
    )


async def _finalize(session, snapshot, *, idempotency_key, **kwargs):
    return await finalize_redemption(
        session,
        uid="google-user",
        email=kwargs.pop("email", "buyer@example.com"),
        original_app_user_id="$RCAnonymousID:web",
        redemption_link_hash=_link_hash(),
        idempotency_key=idempotency_key,
        environment="SANDBOX",
        provider_snapshot=snapshot,
        **kwargs,
    )


def _now():
    return datetime.now(UTC).replace(microsecond=0)


@pytest.mark.asyncio
async def test_finalization_attaches_purchase_to_authenticated_user():
    binding = _finalize_binding()
    binding.provider = "revenuecat"
    binding.project = "default"
    binding.entitlement_id = "standard"
    lead = _lead()
    lead.snapshot = _snapshot(target_calories=None)
    now = _now()
    expires = now + timedelta(days=30)
    session = _FinalizeSession(binding, lead)

    result = await _finalize(
        session,
        _web_snapshot(now),
        idempotency_key="request-google-user",
        email="BUYER@example.com",
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
    assert session.committed is True
    assert result["access_status"] == "active"
    macros = result["macros"]
    assert macros["calories"] == pytest.approx(
        macros["protein"] * 4 + macros["carbs"] * 4 + macros["fat"] * 9
    )
    assert macros["calories"] != 2000


@pytest.mark.asyncio
async def test_finalization_only_matches_revenuecat_rows_and_keeps_transaction_id():
    lead = _lead()
    lead.snapshot = _snapshot()
    existing = _web_row(store_transaction_id="sub_stripe_1")
    momo = _web_row(
        id="momo-row",
        provider="momo",
        product_id="momo_pass_30d",
        revenuecat_subscriber_id=None,
    )
    session = _FinalizeSession(
        _finalize_binding(),
        lead,
        _owner(),
        profile=object(),
        budget=object(),
        rows=[existing, momo],
    )
    now = _now()

    await _finalize(
        session,
        _web_snapshot(now, store_transaction_id=None),
        idempotency_key="request-existing-row",
    )

    rows_sql = str(
        session.statements[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "subscriptions.provider = 'revenuecat'" in rows_sql
    assert not [item for item in session.added if isinstance(item, Subscription)]
    assert existing.provider_snapshot_at == now
    assert existing.store_transaction_id == "sub_stripe_1"
    assert (momo.status, momo.provider_snapshot_at) == ("active", None)


@pytest.mark.asyncio
async def test_finalization_inserts_a_verified_row_with_every_normalized_fact():
    """A new web buyer's row must be verified, or entitlements answer 503."""
    from src.domain.model.subscription_access import evaluate_subscription_access
    from src.infra.services.subscription_access_sync_service import (
        normalize_standard_access_facts,
    )

    lead = _lead()
    lead.snapshot = _snapshot(target_calories=None)
    session = _FinalizeSession(_finalize_binding(), lead)
    now = _now()
    snapshot = _web_snapshot(
        now,
        expires_in=timedelta(days=1),
        period_type="trial",
        billing_issues_detected_at=(now - timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        grace_period_expires_date=(now + timedelta(days=6)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        unsubscribe_detected_at=(now - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    )
    facts = normalize_standard_access_facts(snapshot.subscriber, now)

    await _finalize(
        session,
        snapshot,
        idempotency_key="request-new-row",
        auth_provider="google.com",
        expires_at=facts["expires_at"],
    )

    (subscription,) = [i for i in session.added if isinstance(i, Subscription)]
    assert subscription.provider == "revenuecat"
    assert subscription.revenuecat_subscriber_id == "google-user"
    assert subscription.provider_snapshot_at == now
    assert subscription.provider_sync_required_at is None
    assert facts["status"] == "billing_issue"
    for column, value in facts.items():
        assert getattr(subscription, column) == value, column
    access = evaluate_subscription_access([subscription], now=now + timedelta(days=3))
    assert (access.has_access, access.status) == (True, "grace_period")


@pytest.mark.asyncio
async def test_finalization_overwrites_stale_facts_on_an_existing_row():
    lead = _lead()
    lead.snapshot = _snapshot()
    now = _now()
    existing = _web_row(
        platform="ios",
        status="expired",
        purchased_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=35),
        grace_period_expires_at=now - timedelta(days=30),
        period_type="normal",
        is_sandbox=False,
        provider_snapshot_at=now - timedelta(days=35),
    )
    session = _FinalizeSession(
        _finalize_binding(),
        lead,
        _owner(),
        profile=object(),
        budget=object(),
        rows=[existing],
    )

    await _finalize(session, _web_snapshot(now), idempotency_key="request-stale-row")

    assert existing.provider_snapshot_at == now
    assert (existing.platform, existing.status, existing.is_sandbox) == (
        "web",
        "active",
        True,
    )
    assert existing.expires_at == now + timedelta(days=30)
    assert existing.grace_period_expires_at is None
    assert existing.store_transaction_id == "sub_stripe_9"
    assert not [item for item in session.added if isinstance(item, Subscription)]


@pytest.mark.asyncio
async def test_finalization_resolves_the_marker_a_failed_webhook_read_left():
    """PURCHASE_REDEEMED could not read RevenueCat and left a placeholder;
    finalize's newer snapshot answers it, so entitlements stop answering 503."""
    from src.domain.model.subscription_access import evaluate_subscription_access

    lead = _lead()
    lead.snapshot = _snapshot()
    now = _now()
    event_time = now - timedelta(minutes=1)
    placeholder = _web_row(
        status="expired",
        purchased_at=event_time,
        provider_sync_required_at=event_time,
    )
    session = _FinalizeSession(
        _finalize_binding(),
        lead,
        _owner(),
        profile=object(),
        budget=object(),
        rows=[placeholder],
    )

    await _finalize(session, _web_snapshot(now), idempotency_key="request-marked-row")

    assert placeholder.provider_sync_required_at is None
    assert evaluate_subscription_access([placeholder], now).has_access is True


@pytest.mark.asyncio
async def test_finalization_keeps_newer_verified_facts_committed_meanwhile():
    """A refresh or webhook committed a newer snapshot between finalize's
    RevenueCat read and its transaction; finalize must not roll it back."""
    lead = _lead()
    lead.snapshot = _snapshot()
    now = _now()
    newer_expiry = now + timedelta(days=60)
    row = _web_row(
        status="cancelled",
        expires_at=newer_expiry,
        period_type="normal",
        provider_snapshot_at=now,
    )
    binding = _finalize_binding()
    session = _FinalizeSession(
        binding, lead, _owner(), profile=object(), budget=object(), rows=[row]
    )

    await _finalize(
        session,
        _web_snapshot(now - timedelta(seconds=5)),
        idempotency_key="request-older-read",
    )

    assert (row.status, row.expires_at, row.provider_snapshot_at) == (
        "cancelled",
        newer_expiry,
        now,
    )
    assert binding.finalized_uid == "google-user"


@pytest.mark.asyncio
async def test_finalization_answers_markers_on_the_users_other_revenuecat_rows():
    """A webhook naming the uid (a TRANSFER, with no product) failed its read
    before finalize and left a marked placeholder. Finalize's snapshot is the
    whole customer: like any sync it stamps every RevenueCat row and clears
    the markers it answers, so the buyer leaves the repair population and
    the gate's sync_required count."""
    from src.domain.model.subscription_access import evaluate_subscription_access

    lead = _lead()
    lead.snapshot = _snapshot()
    now = _now()
    event_time = now - timedelta(minutes=2)
    placeholder = _web_row(
        id="placeholder",
        product_id="unknown",
        platform="ios",
        status="expired",
        purchased_at=event_time,
        provider_sync_required_at=event_time,
        is_sandbox=False,
    )
    old_store_row = _web_row(
        id="old-annual",
        product_id="annual",
        platform="ios",
        status="cancelled",
        expires_at=now - timedelta(days=3),
        provider_snapshot_at=now - timedelta(days=10),
        provider_sync_required_at=event_time,
        is_sandbox=False,
    )
    session = _FinalizeSession(
        _finalize_binding(),
        lead,
        _owner(),
        profile=object(),
        budget=object(),
        rows=[placeholder, old_store_row],
    )

    await _finalize(session, _web_snapshot(now), idempotency_key="request-other-rows")

    (created,) = [i for i in session.added if isinstance(i, Subscription)]
    assert created.product_id == "web_monthly"
    rows = [placeholder, old_store_row, created]
    assert [row.provider_sync_required_at for row in rows] == [None, None, None]
    assert [row.provider_snapshot_at for row in rows] == [now, now, now]
    assert (placeholder.status, old_store_row.status) == ("expired", "expired")
    access = evaluate_subscription_access(rows, now)
    assert (access.has_access, access.source) == (True, created)


@pytest.mark.asyncio
async def test_finalization_without_a_snapshot_writes_nothing():
    lead = _lead()
    lead.snapshot = _snapshot()
    session = _FinalizeSession(
        _finalize_binding(), lead, _owner(), profile=object(), budget=object()
    )

    with pytest.raises(ValueError, match="verified RevenueCat snapshot"):
        await _finalize(session, None, idempotency_key="request-no-snapshot")

    assert session.committed is False
