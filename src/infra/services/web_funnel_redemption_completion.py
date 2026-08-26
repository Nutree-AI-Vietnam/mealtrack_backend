"""Atomic onboarding restoration after a provider-verified RevenueCat redemption."""

import hashlib
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.auth import AuthProvider
from src.domain.model.user import Goal, JobType, Sex, TdeeRequest, TrainingLevel
from src.domain.services.tdee_service import TdeeCalculationService
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.database.models.weekly.weekly_macro_budget import WeeklyMacroBudgetORM


def utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def claim_conflict():
    return HTTPException(status_code=409, detail="Claim conflict")


def existing_account_sign_in_required():
    return HTTPException(
        status_code=409,
        detail={
            "code": "EXISTING_ACCOUNT_REQUIRES_SIGN_IN",
            "message": "Sign in to the existing Nutree account to continue.",
        },
    )


def claim_not_found():
    return HTTPException(status_code=404, detail="Claim not found")


def _age(snapshot):
    born = date(snapshot["birth_year"], snapshot["birth_month"], snapshot["birth_day"])
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _username(email):
    return email.split("@", 1)[0][:50]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _auth_provider(firebase_provider: str | None) -> AuthProvider:
    if firebase_provider == "apple.com":
        return AuthProvider.APPLE
    if firebase_provider == "password":
        return AuthProvider.EMAIL_LINK
    return AuthProvider.GOOGLE


def _week_start(today):
    return today.fromordinal(today.toordinal() - today.weekday())


def _result(snapshot, uid):
    age = _age(snapshot)
    request = TdeeRequest(
        age=age,
        sex=Sex(snapshot["gender"]),
        height=snapshot["height"],
        weight=snapshot["weight"],
        body_fat_pct=snapshot.get("body_fat_percentage"),
        job_type=JobType(snapshot["job_type"]),
        training_days_per_week=snapshot["training_days_per_week"],
        training_minutes_per_session=snapshot["training_minutes_per_session"],
        goal=Goal(snapshot["goal"]),
        training_level=(
            TrainingLevel(snapshot["training_level"])
            if snapshot.get("training_level")
            else None
        ),
    )
    calculation = TdeeCalculationService().calculate_tdee(request)
    macros = calculation.macros
    custom_macro_keys = ("custom_protein_g", "custom_carbs_g", "custom_fat_g")
    if all(snapshot.get(key) is not None for key in custom_macro_keys):
        protein, carbs, fat = (float(snapshot[key]) for key in custom_macro_keys)
        macro_data = {
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "calories": round(protein * 4 + carbs * 4 + fat * 9, 1),
        }
    else:
        macro_data = {
            "protein": macros.protein,
            "carbs": macros.carbs,
            "fat": macros.fat,
            "calories": macros.calories,
        }
    return {
        "firebase_uid": uid,
        "onboarding_completed": True,
        "age": age,
        "tdee": calculation.tdee,
        "macros": macro_data,
    }


async def finalize_redemption(
    db: AsyncSession,
    *,
    uid: str,
    email: str | None,
    original_app_user_id: str,
    redemption_link_hash: str,
    idempotency_key: str,
    environment: str,
    auth_provider: str | None = None,
    expires_at=None,
) -> dict:
    """Restore one paid lead once; hash + preflight UID select the exact row."""
    binding = await db.scalar(
        select(WebFunnelRedemption)
        .where(
            WebFunnelRedemption.environment == environment,
            WebFunnelRedemption.redemption_link_hash == redemption_link_hash,
            WebFunnelRedemption.preflight_uid == uid,
        )
        .with_for_update()
    )
    if not binding:
        raise claim_not_found()
    aliases = set(binding.provider_app_user_ids or [])
    if (
        binding.original_app_user_id != original_app_user_id
        and original_app_user_id not in aliases
        and binding.verified_app_user_id != original_app_user_id
    ):
        raise claim_not_found()
    if binding.redeemer_uid and binding.redeemer_uid != uid:
        raise claim_not_found()
    binding.redeemer_uid = uid
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    if binding.finalized_uid:
        if binding.finalized_uid != uid:
            raise claim_conflict()
        return binding.result or {
            "version": "redemption_result_v1",
            "access_status": "active",
        }
    lead = await db.get(WebFunnelLead, binding.lead_id, with_for_update=True)
    if not lead or lead.status in {"refunded", "revoked", "conflict"}:
        raise claim_not_found()
    if email is None or _normalize_email(email) != _normalize_email(lead.email):
        raise claim_conflict()
    if expires_at is not None and expires_at <= utcnow():
        raise claim_not_found()
    user = await db.scalar(
        select(User).where(User.firebase_uid == uid).with_for_update()
    )
    email_owner = await db.scalar(
        select(User)
        .where(func.lower(User.email) == _normalize_email(lead.email))
        .with_for_update()
    )
    if email_owner and email_owner.firebase_uid != uid:
        raise existing_account_sign_in_required()
    if user and _normalize_email(user.email) != _normalize_email(lead.email):
        raise claim_conflict()
    if user is None:
        user = User(
            firebase_uid=uid,
            email=lead.email,
            username=_username(lead.email),
            password_hash="",
            provider=_auth_provider(auth_provider),
            onboarding_completed=False,
        )
        db.add(user)
        await db.flush()
    user.onboarding_completed = True
    user.revenuecat_customer_id = uid
    snapshot = lead.snapshot
    profile = await db.scalar(
        select(UserProfile)
        .where(UserProfile.user_id == user.id, UserProfile.is_current.is_(True))
        .with_for_update()
    )
    if profile is None:
        db.add(
            UserProfile(
                user_id=user.id,
                age=_age(snapshot),
                gender=snapshot["gender"],
                height_cm=snapshot["height"],
                weight_kg=snapshot["weight"],
                body_fat_percentage=snapshot.get("body_fat_percentage"),
                date_of_birth=date(
                    snapshot["birth_year"],
                    snapshot["birth_month"],
                    snapshot["birth_day"],
                ),
                job_type=snapshot["job_type"],
                training_days_per_week=snapshot["training_days_per_week"],
                training_minutes_per_session=snapshot["training_minutes_per_session"],
                fitness_goal=snapshot["goal"],
                meals_per_day=snapshot.get("meals_per_day", 3),
                pain_points=snapshot.get("pain_points", []),
                dietary_preferences=snapshot.get("dietary_preferences", []),
                training_level=snapshot.get("training_level"),
                challenge_duration=snapshot.get("challenge_duration"),
                training_types=snapshot.get("training_types"),
                custom_protein_g=snapshot.get("custom_protein_g"),
                custom_carbs_g=snapshot.get("custom_carbs_g"),
                custom_fat_g=snapshot.get("custom_fat_g"),
                target_weight_kg=snapshot.get("target_weight_kg"),
            )
        )
    result = {
        **_result(snapshot, uid),
        "version": "redemption_result_v1",
        "access_status": "active",
    }
    week_start = _week_start(date.today())
    budget = await db.scalar(
        select(WeeklyMacroBudgetORM)
        .where(
            WeeklyMacroBudgetORM.user_id == user.id,
            WeeklyMacroBudgetORM.week_start_date == week_start,
        )
        .with_for_update()
    )
    if budget is None:
        macros = result["macros"]
        db.add(
            WeeklyMacroBudgetORM(
                weekly_budget_id=str(uuid.uuid4()),
                user_id=user.id,
                week_start_date=week_start,
                target_calories=macros["calories"] * 7,
                target_protein=macros["protein"] * 7,
                target_carbs=macros["carbs"] * 7,
                target_fat=macros["fat"] * 7,
            )
        )
    subscription = await db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.revenuecat_subscriber_id == uid,
            Subscription.product_id == binding.product_id,
        )
        .with_for_update()
    )
    if subscription is None:
        db.add(
            Subscription(
                user_id=user.id,
                revenuecat_subscriber_id=uid,
                product_id=binding.product_id,
                platform="web",
                status="active",
                purchased_at=utcnow(),
                expires_at=expires_at,
                is_sandbox=binding.environment.upper() == "SANDBOX",
            )
        )
    else:
        subscription.status = "active"
        subscription.expires_at = expires_at
    binding.finalized_uid, binding.finalized_at = uid, utcnow()
    binding.finalization_key_hash, binding.result = key_hash, result
    lead.claimed_uid, lead.claimed_at, lead.status, lead.access_sync_status = (
        uid,
        utcnow(),
        "claimed",
        "active",
    )
    await db.commit()
    return result
