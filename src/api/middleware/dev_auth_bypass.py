import logging
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from src.infra.database.config_async import AsyncSessionLocal
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.meal.meal_image import MealImageORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.user import User

logger = logging.getLogger(__name__)


async def _ensure_dev_user_async() -> User | None:
    """Create or fetch the single development user and profile (async).

    This is safe to call multiple times. Returns the persisted SQLAlchemy User instance
    loaded from a short-lived session; do not store it for reuse across requests.

    Returns None if database isn't ready (e.g., during test collection).
    """
    firebase_uid = os.getenv("DEV_USER_FIREBASE_UID", "dev_firebase_uid")
    email = os.getenv("DEV_USER_EMAIL", "dev@example.com")
    username = os.getenv("DEV_USER_USERNAME", "dev_user")

    if AsyncSessionLocal is None:
        return None

    session = AsyncSessionLocal()
    try:
        user = await _load_existing_dev_user(
            session, firebase_uid=firebase_uid, email=email, username=username
        )
        if user:
            return user

        # Create new dev user
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            username=username,
            first_name="Dev",
            last_name="User",
            password_hash="dev_password_placeholder",
            is_active=True,
            onboarding_completed=True,
        )
        session.add(user)
        await session.flush()

        # Create a basic current profile
        profile = UserProfile(
            user_id=user.id,
            age=30,
            gender="male",
            height_cm=175.0,
            weight_kg=70.0,
            body_fat_percentage=18.0,
            is_current=True,
            job_type="desk",
            training_days_per_week=4,
            training_minutes_per_session=60,
            fitness_goal="recomp",
            referral_sources=["tiktok", "friend_family", "google_search"],
            target_weight_kg=70.0,
            meals_per_day=3,
            snacks_per_day=1,
            dietary_preferences=[],
            health_conditions=[],
            allergies=[],
            pain_points=[],
        )
        session.add(profile)
        await session.commit()

        logger.info("Created development user '%s' (%s)", username, user.id)
        return user
    except IntegrityError:
        await session.rollback()
        # Another request (or a previous Firebase login) already created this
        # email/uid. Reuse that row instead of failing the bypass.
        user = await _load_existing_dev_user(
            session, firebase_uid=firebase_uid, email=email, username=username
        )
        if user:
            return user
        logger.warning(
            "Failed to ensure dev user after unique conflict for email=%s uid=%s",
            email,
            firebase_uid,
        )
        return None
    except Exception as exc:
        await session.rollback()
        logger.warning("Failed to ensure dev user (database may not be ready): %s", exc)
        return None
    finally:
        await session.close()


async def _load_existing_dev_user(
    session, *, firebase_uid: str, email: str, username: str
) -> User | None:
    result = await session.execute(
        select(User).where(User.firebase_uid == firebase_uid)
    )
    user = result.scalars().first()
    if user:
        return user
    result = await session.execute(
        select(User).where(or_(User.email == email, User.username == username))
    )
    return result.scalars().first()


def add_dev_auth_bypass(app: FastAPI) -> None:
    """Install a dev-only middleware that sets request.state.user.

    The injected user has an `id` attribute and a `has_active_subscription()` method that returns True
    so subscription-only endpoints work in development.
    """
    # Defense in depth: require BOTH a non-prod environment AND an explicit
    # opt-in flag, so a single stray ENVIRONMENT=development in production can't
    # silently disable Firebase auth.
    if (
        os.getenv("ENVIRONMENT") != "development"
        or os.getenv("ENABLE_DEV_AUTH_BYPASS") != "1"
    ):
        logger.info(
            "Dev auth bypass not enabled "
            "(needs ENVIRONMENT=development and ENABLE_DEV_AUTH_BYPASS=1)"
        )
        return

    @app.middleware("http")
    async def dev_user_injector(request: Request, call_next):  # type: ignore[override]
        # Load fresh per request to avoid cross-session ORM usage
        user = await _ensure_dev_user_async()
        if user is not None:
            # Provide only what downstream code expects
            request.state.user = SimpleNamespace(
                id=user.id,
                firebase_uid=user.firebase_uid,
                email=user.email,
                username=user.username,
                has_active_subscription=lambda: True,
            )

        return await call_next(request)


async def _seed_dev_meals_async(user_id: str) -> None:
    """Seed a small set of meals with nutrition for today's date if none exist (async).
    Creates breakfast, lunch, dinner with simple nutrition totals so daily macros works.
    """
    if AsyncSessionLocal is None:
        return

    session = AsyncSessionLocal()
    try:
        # Check if there are any meals today for this user (use UTC to match created_at)
        from datetime import timedelta

        today_utc = datetime.now(timezone.utc).date()
        start_dt = datetime.combine(today_utc, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        existing = await session.scalar(
            select(func.count())
            .select_from(MealORM)
            .where(
                MealORM.user_id == user_id,
                MealORM.created_at >= start_dt,
                MealORM.created_at < end_dt,
            )
        )
        if existing > 0:
            # Backfill missing ready_at for READY meals created earlier without it
            result = await session.execute(
                select(MealORM).where(
                    MealORM.user_id == user_id,
                    MealORM.created_at >= start_dt,
                    MealORM.created_at < end_dt,
                    MealORM.status == MealStatusEnum.READY,
                    MealORM.ready_at.is_(None),
                )
            )
            meals_missing_ready = result.scalars().all()
            if meals_missing_ready:
                now = datetime.now(timezone.utc)
                for m in meals_missing_ready:
                    m.ready_at = m.created_at or now
                    m.updated_at = now
                await session.commit()
            return

        def create_meal(meal_name: str, p: float, c: float, f: float):
            meal_id = str(uuid.uuid4())
            image_id = str(uuid.uuid4())
            # Minimal image row (FK is required)
            db_image = MealImageORM(
                image_id=image_id,
                format="jpeg",
                size_bytes=12345,
                width=800,
                height=600,
                url=None,
            )
            session.add(db_image)

            now = datetime.now(timezone.utc)
            db_meal = MealORM(
                meal_id=meal_id,
                user_id=user_id,
                status=MealStatusEnum.READY,
                dish_name=meal_name,
                image_id=image_id,
                created_at=now,
                updated_at=now,
                ready_at=now.replace(tzinfo=None),  # Column is naive DateTime
            )
            session.add(db_meal)

            db_nutrition = NutritionORM(
                protein=p,
                carbs=c,
                fat=f,
                confidence_score=0.95,
                meal_id=meal_id,
            )
            session.add(db_nutrition)

        # Seed three meals (calories derived from macros: P*4 + C*4 + F*9)
        create_meal("Breakfast Oatmeal", 20.0, 60.0, 10.0)
        create_meal("Chicken Salad Lunch", 50.0, 30.0, 30.0)
        create_meal("Salmon Dinner", 45.0, 40.0, 35.0)

        await session.commit()
        logger.info("Seeded dev meals for user %s", user_id)
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to seed dev meals: %s", exc)
    finally:
        await session.close()
