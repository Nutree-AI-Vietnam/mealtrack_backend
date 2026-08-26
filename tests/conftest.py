"""
Global pytest configuration and fixtures.
"""

from collections.abc import Generator
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, create_engine, func, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, noload, selectinload, sessionmaker
from tests.fixtures.database.test_config import (
    create_test_engine,
    get_test_database_url,
)
from tests.fixtures.mock_image_store import MockImageStore

from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.meal_projection import MealProjection
from src.domain.parsers.gpt_response_parser import GPTResponseParser
from src.domain.utils.timezone_utils import get_zone_info, utc_now
from src.infra.database.base import Base

# Import all models to ensure they're registered with Base metadata
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.food_item_translation_model import (
    FoodItemTranslationORM,
)
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.meal.meal_image import MealImageORM
from src.infra.database.models.meal.meal_translation_model import MealTranslationORM
from src.infra.database.models.nutrition.food_item import FoodItemORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.user import User
from src.infra.event_bus import EventBus, PyMediatorEventBus
from src.infra.mappers import MealStatusMapper
from src.infra.mappers.meal_mapper import (
    _instruction_steps_to_orm,
    food_item_domain_to_orm,
    meal_domain_to_orm,
    meal_image_domain_to_orm,
    meal_orm_to_domain,
    nutrition_domain_to_orm,
)
from src.infra.mappers.user_mapper import (
    UserMapper,
    UserProfileMapper,
    build_profile_preference_entries,
)

TEST_MEAL_PROJECTION_OPTS: dict = {
    MealProjection.MACROS_ONLY: (
        noload(MealORM.image),
        selectinload(MealORM.nutrition).selectinload(NutritionORM.food_items),
        selectinload(MealORM.instruction_steps),
    ),
    MealProjection.FULL: (
        joinedload(MealORM.image),
        selectinload(MealORM.nutrition).selectinload(NutritionORM.food_items),
        selectinload(MealORM.instruction_steps),
    ),
    MealProjection.FULL_WITH_TRANSLATIONS: (
        joinedload(MealORM.image),
        selectinload(MealORM.nutrition).selectinload(NutritionORM.food_items),
        selectinload(MealORM.instruction_steps),
        joinedload(MealORM.translations),
    ),
}


def _test_domain_hydratable_active_meal_filter():
    return and_(
        MealORM.status != MealStatusEnum.INACTIVE,
        or_(
            MealORM.status != MealStatusEnum.READY,
            and_(MealORM.ready_at.is_not(None), MealORM.nutrition.has()),
        ),
    )


class TestMealRepository:
    """Sync-session repository facade used only by legacy SQLite tests."""

    def __init__(self, session: Session):
        self.db = session

    def save(self, meal: Meal) -> Meal:
        existing_meal = (
            self.db.query(MealORM)
            .options(
                selectinload(MealORM.nutrition).selectinload(NutritionORM.food_items),
                selectinload(MealORM.instruction_steps),
            )
            .filter(MealORM.meal_id == meal.meal_id)
            .first()
        )
        if existing_meal:
            existing_meal.status = MealStatusMapper.to_db(meal.status)
            existing_meal.dish_name = meal.dish_name
            existing_meal.meal_type = meal.meal_type
            existing_meal.ready_at = meal.ready_at
            existing_meal.error_message = meal.error_message
            existing_meal.raw_ai_response = meal.raw_gpt_json
            existing_meal.food_label_metadata = meal.food_label_metadata
            existing_meal.updated_at = meal.updated_at or utc_now()
            existing_meal.last_edited_at = meal.last_edited_at
            existing_meal.edit_count = meal.edit_count
            existing_meal.is_manually_edited = meal.is_manually_edited
            existing_meal.emoji = meal.emoji
            existing_meal.description = meal.description
            existing_meal.instructions = meal.instructions
            existing_meal.prep_time_min = meal.prep_time_min
            existing_meal.cook_time_min = meal.cook_time_min
            existing_meal.cuisine_type = meal.cuisine_type
            existing_meal.origin_country = meal.origin_country
            existing_meal.instruction_steps = _instruction_steps_to_orm(
                meal.instructions
            )
            if meal.image and meal.image.url:
                existing_image = (
                    self.db.query(MealImageORM)
                    .filter(MealImageORM.image_id == meal.image.image_id)
                    .first()
                )
                if existing_image and existing_image.url != meal.image.url:
                    existing_image.url = meal.image.url
            if meal.nutrition:
                if existing_meal.nutrition is None:
                    existing_meal.nutrition = nutrition_domain_to_orm(
                        meal.nutrition, meal_id=meal.meal_id
                    )
                else:
                    self._update_nutrition(existing_meal.nutrition, meal.nutrition)
            self.db.commit()
        else:
            db_meal = meal_domain_to_orm(meal)
            if meal.image:
                existing_image = (
                    self.db.query(MealImageORM)
                    .filter(MealImageORM.image_id == meal.image.image_id)
                    .first()
                )
                if not existing_image:
                    self.db.add(meal_image_domain_to_orm(meal.image))
                    self.db.flush()
                db_meal.image_id = str(meal.image.image_id)
            self.db.add(db_meal)
            self.db.commit()

        persisted = (
            self.db.query(MealORM)
            .options(
                selectinload(MealORM.nutrition).selectinload(NutritionORM.food_items),
                selectinload(MealORM.instruction_steps),
            )
            .filter(MealORM.meal_id == meal.meal_id)
            .first()
        )
        return meal_orm_to_domain(persisted)

    def find_by_id(
        self, meal_id: str, projection: MealProjection = MealProjection.FULL
    ) -> Meal | None:
        entity = (
            self.db.query(MealORM)
            .options(*TEST_MEAL_PROJECTION_OPTS[projection])
            .filter(MealORM.meal_id == meal_id)
            .first()
        )
        return meal_orm_to_domain(entity) if entity else None

    def delete(self, meal_id: str) -> None:
        nutrition = (
            self.db.query(NutritionORM).filter(NutritionORM.meal_id == meal_id).first()
        )
        if nutrition:
            self.db.execute(
                update(FoodItemORM)
                .where(FoodItemORM.nutrition_id == nutrition.id)
                .values(is_deleted=True, nutrition_id=None)
            )

        meal_translation_ids = [
            mt.id
            for mt in self.db.query(MealTranslationORM.id)
            .filter(MealTranslationORM.meal_id == meal_id)
            .all()
        ]
        self.db.execute(
            update(MealTranslationORM)
            .where(MealTranslationORM.meal_id == meal_id)
            .values(is_deleted=True, meal_id=None)
        )
        if meal_translation_ids:
            self.db.execute(
                update(FoodItemTranslationORM)
                .where(
                    FoodItemTranslationORM.meal_translation_id.in_(meal_translation_ids)
                )
                .values(is_deleted=True)
            )
        self.db.execute(
            NutritionORM.__table__.delete().where(NutritionORM.meal_id == meal_id)
        )
        self.db.execute(MealORM.__table__.delete().where(MealORM.meal_id == meal_id))

    def find_by_date(
        self,
        date_obj: date,
        user_id: str = None,
        limit: int = 50,
        user_timezone: str | None = None,
        projection: MealProjection = MealProjection.FULL,
    ) -> list[Meal]:
        tz = get_zone_info(user_timezone) if user_timezone else timezone.utc
        start_dt = datetime.combine(
            date_obj, datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        query = (
            self.db.query(MealORM)
            .options(*TEST_MEAL_PROJECTION_OPTS[projection])
            .filter(MealORM.created_at >= start_dt)
            .filter(MealORM.created_at < end_dt)
        )
        if user_id:
            query = query.filter(MealORM.user_id == user_id)
        rows = (
            query.filter(_test_domain_hydratable_active_meal_filter())
            .order_by(MealORM.created_at.desc())
            .limit(limit)
            .all()
        )
        return [meal_orm_to_domain(m) for m in rows]

    def find_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        limit: int = 500,
        user_timezone: str | None = None,
        projection: MealProjection = MealProjection.FULL,
    ) -> list[Meal]:
        tz = get_zone_info(user_timezone) if user_timezone else timezone.utc
        start_dt = datetime.combine(
            start_date, datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt = (
            datetime.combine(end_date, datetime.min.time(), tzinfo=tz)
            + timedelta(days=1)
        ).astimezone(timezone.utc)
        rows = (
            self.db.query(MealORM)
            .options(*TEST_MEAL_PROJECTION_OPTS[projection])
            .filter(MealORM.created_at >= start_dt)
            .filter(MealORM.created_at < end_dt)
            .filter(MealORM.user_id == user_id)
            .filter(_test_domain_hydratable_active_meal_filter())
            .order_by(MealORM.created_at.asc())
            .limit(limit)
            .all()
        )
        return [meal_orm_to_domain(m) for m in rows]

    def sum_hydration_ml_for_date(
        self, date_obj: date, user_id: str, user_timezone: str | None = None
    ) -> int:
        tz = get_zone_info(user_timezone) if user_timezone else timezone.utc
        start_dt = datetime.combine(
            date_obj, datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        return (
            self.db.query(func.coalesce(func.sum(MealORM.quantity), 0))
            .filter(
                MealORM.user_id == user_id,
                MealORM.meal_type == "hydration",
                MealORM.created_at >= start_dt,
                MealORM.created_at < end_dt,
                _test_domain_hydratable_active_meal_filter(),
            )
            .scalar()
        )

    def sum_hydration_ml_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, int]:
        tz = get_zone_info(user_timezone) if user_timezone else timezone.utc
        start_dt = datetime.combine(
            start_date, datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt = (
            datetime.combine(end_date, datetime.min.time(), tzinfo=tz)
            + timedelta(days=1)
        ).astimezone(timezone.utc)
        date_expr = func.date(MealORM.created_at)
        rows = (
            self.db.query(date_expr, func.coalesce(func.sum(MealORM.quantity), 0))
            .filter(
                MealORM.user_id == user_id,
                MealORM.meal_type == "hydration",
                MealORM.created_at >= start_dt,
                MealORM.created_at < end_dt,
                _test_domain_hydratable_active_meal_filter(),
            )
            .group_by(date_expr)
            .all()
        )
        out: dict[date, int] = {}
        for day_val, total in rows:
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            out[day_val] = int(total)
        return out

    def get_daily_meal_counts(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, int]:
        tz = get_zone_info(user_timezone) if user_timezone else timezone.utc
        start_dt = datetime.combine(
            start_date, datetime.min.time(), tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt = (
            datetime.combine(end_date, datetime.min.time(), tzinfo=tz)
            + timedelta(days=1)
        ).astimezone(timezone.utc)
        date_expr = func.date(MealORM.created_at)
        rows = (
            self.db.query(date_expr, func.count())
            .filter(
                MealORM.user_id == user_id,
                MealORM.created_at >= start_dt,
                MealORM.created_at < end_dt,
                _test_domain_hydratable_active_meal_filter(),
            )
            .group_by(date_expr)
            .all()
        )
        out: dict[date, int] = {}
        for day_val, count in rows:
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            out[day_val] = count
        return out

    def get_dates_with_meals(
        self, user_id: str, user_timezone: str | None = None
    ) -> list[date]:
        date_expr = func.date(MealORM.created_at)
        rows = (
            self.db.query(date_expr)
            .filter(
                MealORM.user_id == user_id,
                _test_domain_hydratable_active_meal_filter(),
            )
            .distinct()
            .order_by(date_expr.desc())
            .all()
        )
        out: list[date] = []
        for (day_val,) in rows:
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            if isinstance(day_val, date):
                out.append(day_val)
        return out

    def count_by_source(self, user_id: str, source: str) -> int:
        return (
            self.db.query(MealORM)
            .filter(MealORM.user_id == user_id, MealORM.source == source)
            .count()
        )

    def _update_nutrition(
        self, db_nutrition: NutritionORM, domain_nutrition: Nutrition
    ) -> None:
        db_nutrition.protein = domain_nutrition.macros.protein
        db_nutrition.carbs = domain_nutrition.macros.carbs
        db_nutrition.fat = domain_nutrition.macros.fat
        db_nutrition.confidence_score = domain_nutrition.confidence_score
        for item in db_nutrition.food_items:
            self.db.delete(item)
        if domain_nutrition.food_items:
            for idx, item in enumerate(domain_nutrition.food_items):
                db_item = food_item_domain_to_orm(item, nutrition_id=db_nutrition.id)
                db_item.order_index = idx
                self.db.add(db_item)


class TestUserRepository:
    """Sync-session repository facade used only by legacy SQLite tests."""

    _USER_LOADS = (
        selectinload(User.profiles).selectinload(UserProfile.preference_entries),
        selectinload(User.subscriptions),
    )

    def __init__(self, session: Session):
        self.db = session

    def save(self, user_domain):
        user_entity = UserMapper.to_persistence(user_domain)
        user_entity.profiles = [
            UserProfileMapper.to_persistence(p) for p in user_domain.profiles
        ]
        if not user_entity.id:
            self.db.add(user_entity)
        else:
            user_entity = self.db.merge(user_entity)
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            error_msg = str(e.orig).lower() if e.orig else str(e).lower()
            if "email" in error_msg:
                raise ValueError("User with this email already exists") from e
            if "firebase_uid" in error_msg:
                raise ValueError("Firebase UID already registered") from e
            raise ValueError("User with this email or username already exists") from e
        user_entity = (
            self.db.query(User)
            .options(*self._USER_LOADS)
            .filter(User.id == str(user_entity.id))
            .first()
        )
        return UserMapper.to_domain(user_entity)

    def find_by_id(self, user_id):
        entity = (
            self.db.query(User)
            .options(*self._USER_LOADS)
            .filter(User.id == str(user_id), User.is_active.is_(True))
            .first()
        )
        return UserMapper.to_domain(entity) if entity else None

    def find_by_email(self, email: str):
        entity = (
            self.db.query(User)
            .options(*self._USER_LOADS)
            .filter(User.email == email, User.is_active.is_(True))
            .first()
        )
        return UserMapper.to_domain(entity) if entity else None

    def find_by_firebase_uid(self, firebase_uid: str):
        entity = (
            self.db.query(User)
            .options(*self._USER_LOADS)
            .filter(User.firebase_uid == firebase_uid, User.is_active.is_(True))
            .first()
        )
        return UserMapper.to_domain(entity) if entity else None

    def find_deleted_by_firebase_uid(self, firebase_uid: str):
        entity = (
            self.db.query(User)
            .options(*self._USER_LOADS)
            .filter(User.firebase_uid == firebase_uid, User.is_active.is_(False))
            .first()
        )
        return UserMapper.to_domain(entity) if entity else None

    def delete(self, user_id) -> bool:
        entity = self.db.query(User).filter(User.id == str(user_id)).first()
        if not entity:
            return False
        entity.is_active = False
        self.db.commit()
        return True

    def get_profile(self, user_id):
        entity = (
            self.db.query(UserProfile)
            .options(selectinload(UserProfile.preference_entries))
            .filter(
                UserProfile.user_id == str(user_id), UserProfile.is_current.is_(True)
            )
            .first()
        )
        return UserProfileMapper.to_domain(entity) if entity else None

    def update_profile(self, profile_domain):
        profile_id = str(profile_domain.id) if profile_domain.id else None
        entity = (
            self.db.query(UserProfile)
            .options(selectinload(UserProfile.preference_entries))
            .filter(UserProfile.id == profile_id)
            .first()
            if profile_id
            else None
        )
        if entity is None:
            entity = UserProfileMapper.to_persistence(profile_domain)
            self.db.add(entity)
        else:
            updated = UserProfileMapper.to_persistence(profile_domain)
            for col in UserProfile.__table__.columns:
                if col.key not in {"id", "created_at", "updated_at"}:
                    setattr(entity, col.key, getattr(updated, col.key, None))
            entity.preference_entries = build_profile_preference_entries(profile_domain)
        self.db.commit()
        self.db.refresh(entity)
        return UserProfileMapper.to_domain(entity)

    def update_user_timezone(self, user_id, timezone: str) -> None:
        self.db.query(User).filter(User.id == str(user_id)).update(
            {"timezone": timezone}
        )
        self.db.commit()

    def get_user_timezone(self, user_id) -> str | None:
        entity = (
            self.db.query(User)
            .filter(User.id == str(user_id), User.is_active.is_(True))
            .first()
        )
        return entity.timezone if entity else None


class AsyncTestMealRepository:
    """Explicit async test facade for legacy sync-session handler tests."""

    def __init__(self, session: Session):
        self._repo = TestMealRepository(session)

    async def find_by_id(self, *args, **kwargs):
        return self._repo.find_by_id(*args, **kwargs)

    async def save(self, *args, **kwargs):
        return self._repo.save(*args, **kwargs)

    async def insert(self, *args, **kwargs):
        return self._repo.save(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        return self._repo.delete(*args, **kwargs)

    async def find_by_date(self, *args, **kwargs):
        return self._repo.find_by_date(*args, **kwargs)

    async def find_by_date_range(self, *args, **kwargs):
        return self._repo.find_by_date_range(*args, **kwargs)

    async def sum_hydration_ml_for_date(self, *args, **kwargs):
        return self._repo.sum_hydration_ml_for_date(*args, **kwargs)

    async def sum_hydration_ml_by_date_range(self, *args, **kwargs):
        return self._repo.sum_hydration_ml_by_date_range(*args, **kwargs)

    async def get_daily_meal_counts(self, *args, **kwargs):
        return self._repo.get_daily_meal_counts(*args, **kwargs)

    async def get_dates_with_meals(self, *args, **kwargs):
        return self._repo.get_dates_with_meals(*args, **kwargs)

    async def count_by_source(self, *args, **kwargs):
        return self._repo.count_by_source(*args, **kwargs)


class AsyncTestUserRepository:
    """Explicit async test facade for legacy sync-session handler tests."""

    def __init__(self, session: Session):
        self._repo = TestUserRepository(session)

    async def save(self, *args, **kwargs):
        return self._repo.save(*args, **kwargs)

    async def find_by_id(self, *args, **kwargs):
        return self._repo.find_by_id(*args, **kwargs)

    async def find_by_email(self, *args, **kwargs):
        return self._repo.find_by_email(*args, **kwargs)

    async def find_by_firebase_uid(self, *args, **kwargs):
        return self._repo.find_by_firebase_uid(*args, **kwargs)

    async def find_deleted_by_firebase_uid(self, *args, **kwargs):
        return self._repo.find_deleted_by_firebase_uid(*args, **kwargs)

    async def get_profile(self, *args, **kwargs):
        return self._repo.get_profile(*args, **kwargs)

    async def update_profile(self, *args, **kwargs):
        return self._repo.update_profile(*args, **kwargs)

    async def update_user_timezone(self, *args, **kwargs):
        return self._repo.update_user_timezone(*args, **kwargs)

    async def get_user_timezone(self, *args, **kwargs):
        return self._repo.get_user_timezone(*args, **kwargs)


class TestUnitOfWork:
    """Test-friendly UoW that doesn't close the session on exit.

    Supports both sync (``with``) and async (``async with``) context managers
    so it works with both legacy sync tests and the new async command handlers.
    """

    def __init__(self, session: Session):
        self.session = session
        self.meals = AsyncTestMealRepository(session)
        self.users = AsyncTestUserRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Don't close session - let the test fixture manage it
        if exc_type:
            self.session.rollback()
        # Don't commit here - tests manage their own commits

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Don't close session - let the test fixture manage it
        if exc_type:
            self.session.rollback()
        # Don't commit here - tests manage their own commits

    async def commit(self):
        # No-op for tests - session already has the data
        pass

    async def rollback(self):
        self.session.rollback()


def _is_db_available() -> bool:
    """Check if the test database is available."""
    try:
        from sqlalchemy import create_engine, text
        from tests.fixtures.database.test_config import get_test_database_url

        url = get_test_database_url()
        engine = create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": 3}
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


# Cache the result to avoid repeated connection checks
_db_available = None


def is_db_available() -> bool:
    """Check if DB is available (cached)."""
    global _db_available
    if _db_available is None:
        _db_available = _is_db_available()
    return _db_available


# Marker to skip tests when DB is unavailable
requires_db = pytest.mark.skipif(not is_db_available(), reason="Database not available")


@pytest.fixture(scope="function")
def event_loop():
    """Create an event loop for each test function."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_ai_model_manager():
    """Reset AI singleton before and after each test to prevent model-pool pollution."""
    from src.infra.services.ai.ai_model_manager import AIModelManager

    AIModelManager.reset_instance()

    yield

    AIModelManager.reset_instance()


@pytest.fixture(scope="session")
def worker_id(request):
    """Get worker ID for parallel testing, defaults to 'master' for non-parallel runs."""
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"


@pytest.fixture(scope="session")
def test_engine(worker_id, request):
    """Create a test database engine.

    For unit tests: Uses SQLite in-memory database (faster, no external dependencies)
    For integration tests: Uses MySQL database (when integration tests are explicitly run)
    For API integration tests: Uses SQLite in-memory (fast, isolated)
    """
    # Try to detect if we're running integration tests
    # Since pytest.ini has --ignore=tests/integration for unit tests by default,
    # if we reach here with integration tests, they must be explicitly run
    try:
        if hasattr(request.session, "items") and request.session.items:
            test_paths = [str(item.fspath) for item in request.session.items]
            has_integration_tests = any(
                "tests/integration" in path for path in test_paths
            )
            # API tests should use SQLite, not MySQL
            has_api_tests = any("tests/integration/api" in path for path in test_paths)
        else:
            # Default to SQLite for unit tests if we can't determine
            has_integration_tests = False
            has_api_tests = False
    except (AttributeError, TypeError):
        # Default to SQLite for unit tests if detection fails
        has_integration_tests = False
        has_api_tests = False

    # Use SQLite in-memory for unit tests and API tests (default)
    # Use real MySQL database only when non-API integration tests are explicitly run
    if not has_integration_tests or has_api_tests:
        # Use SQLite in-memory database for unit tests
        engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, echo=False
        )

        # Import all models to ensure they're registered with Base.metadata
        from src.infra.database import models  # noqa: F401

        # Create all tables
        Base.metadata.create_all(bind=engine)

        yield engine
        engine.dispose()
    else:
        # Use real database for integration tests
        engine = create_test_engine()

        # Create test database if it doesn't exist
        temp_engine = create_engine(
            get_test_database_url().rsplit("/", 1)[0], isolation_level="AUTOCOMMIT"
        )
        try:
            with temp_engine.connect() as conn:
                db_name = get_test_database_url().rsplit("/", 1)[1].split("?")[0]
                if temp_engine.dialect.name == "postgresql":
                    exists = conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                        {"db_name": db_name},
                    ).scalar()
                    if not exists:
                        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                elif temp_engine.dialect.name == "mysql":
                    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))
        finally:
            temp_engine.dispose()

        # Import all models to ensure they're registered with Base.metadata
        from src.infra.database import models  # noqa: F401

        # Only one worker should create tables to avoid race conditions
        if worker_id in ("master", "gw0"):
            # Drop all tables first to ensure clean state
            with engine.begin() as conn:
                if engine.dialect.name == "mysql":
                    conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                Base.metadata.drop_all(bind=engine)
                if engine.dialect.name == "mysql":
                    conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

            # Create all tables
            Base.metadata.create_all(bind=engine)

        # Other workers wait for tables to be created
        elif worker_id != "master":
            import time

            from sqlalchemy import inspect

            # Wait up to 30 seconds for tables to be created
            max_wait = 30
            wait_interval = 0.5
            waited = 0

            while waited < max_wait:
                try:
                    inspector = inspect(engine)
                    tables = inspector.get_table_names()
                    # Check if key tables exist
                    if (
                        "nutrition" in tables
                        and "meal" in tables
                        and "food_item" in tables
                    ):
                        break
                except Exception:
                    pass

                time.sleep(wait_interval)
                waited += wait_interval

            # If tables still don't exist, try creating them ourselves
            if waited >= max_wait:
                Base.metadata.create_all(bind=engine)

        yield engine
        engine.dispose()


@pytest.fixture(scope="function")
def test_session(test_engine) -> Generator[Session]:
    """Create a test database session with rollback after each test."""
    # Create a new connection for each test
    connection = test_engine.connect()

    # Start a transaction
    transaction = connection.begin()

    # Create a session bound to this connection
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = SessionLocal()

    # Configure the session to use this specific connection
    session.connection = connection

    try:
        yield session
    finally:
        # Always clean up, even if test fails
        session.close()
        try:
            transaction.rollback()
        except Exception:
            pass  # Transaction might already be closed
        try:
            connection.close()
        except Exception:
            pass  # Connection might already be closed


@pytest.fixture(autouse=True)
def reset_event_bus_singleton():
    """
    Reset the event bus singleton before each test to prevent state leakage.
    This ensures that event bus initialization happens fresh for each test.
    """

    # Reset singleton before test
    import src.api.dependencies.event_bus as event_bus_module

    event_bus_module._configured_event_bus = None

    yield

    # Reset singleton after test
    event_bus_module._configured_event_bus = None


@pytest.fixture
def mock_image_store() -> MockImageStore:
    """Mock image store for testing."""
    return MockImageStore()


@pytest.fixture
def mock_vision_service():
    """Mock vision AI service for testing."""
    from tests.fixtures.mock_adapters.mock_vision_ai_service import MockVisionAIService

    return MockVisionAIService()


@pytest.fixture
def gpt_parser() -> GPTResponseParser:
    """GPT response parser for testing."""
    return GPTResponseParser()


@pytest.fixture
def meal_repository(test_session) -> TestMealRepository:
    """Meal repository with test database session."""
    return TestMealRepository(test_session)


@pytest.fixture
def strict_session(test_session) -> Session:
    """
    Session configured for N+1 detection testing.

    Use this fixture in tests where you want to verify that
    all necessary relationships are eager loaded. Apply raiseload('*')
    in query options to raise exceptions on lazy loads.

    Example:
        def test_no_n1_queries(strict_session):
            result = strict_session.query(Model).options(raiseload('*')).all()
            # Accessing relationships will raise if not eager loaded
    """
    test_session.expire_on_commit = False
    return test_session


@pytest.fixture
def event_bus(
    test_session, mock_image_store, mock_vision_service, gpt_parser
) -> EventBus:
    """Configured event bus for testing."""
    # Import handlers from modules
    from src.app.commands.meal.edit_meal_command import (
        AddCustomIngredientCommand,
        EditMealCommand,
    )

    # Import commands and queries
    from src.app.commands.meal.upload_meal_image_immediately_command import (
        UploadMealImageImmediatelyCommand,
    )
    from src.app.commands.user.save_user_onboarding_command import (
        SaveUserOnboardingCommand,
    )
    from src.app.handlers.command_handlers import (
        AddCustomIngredientCommandHandler,
        DeleteMealCommandHandler,
        EditMealCommandHandler,
        SaveUserOnboardingCommandHandler,
        UploadMealImageImmediatelyHandler,
    )
    from src.app.handlers.query_handlers import (
        GetDailyMacrosQueryHandler,
        GetMealByIdQueryHandler,
        GetUserProfileQueryHandler,
    )
    from src.app.queries.meal.get_daily_macros_query import GetDailyMacrosQuery
    from src.app.queries.meal.get_meal_by_id_query import GetMealByIdQuery
    from src.app.queries.user.get_user_profile_query import GetUserProfileQuery

    event_bus = PyMediatorEventBus()

    # Create test UoW using the test session (doesn't close session on exit)
    test_uow = TestUnitOfWork(session=test_session)

    # Register meal edit command handlers
    event_bus.register_handler(
        EditMealCommand,
        EditMealCommandHandler(
            uow=test_uow,  # Use test UoW with test session
        ),
    )

    event_bus.register_handler(
        AddCustomIngredientCommand,
        AddCustomIngredientCommandHandler(
            uow=test_uow,  # Use test UoW with test session
        ),
    )

    # Delete (soft delete) handler
    from src.app.commands.meal.delete_meal_command import DeleteMealCommand

    event_bus.register_handler(
        DeleteMealCommand,
        DeleteMealCommandHandler(uow=test_uow),  # Use test UoW
    )

    event_bus.register_handler(
        UploadMealImageImmediatelyCommand,
        UploadMealImageImmediatelyHandler(
            uow=test_uow,
            event_bus=event_bus,
            image_store=mock_image_store,
            vision_service=mock_vision_service,
            gpt_parser=gpt_parser,
        ),
    )

    # Register query handlers
    # These handlers now use UnitOfWork internally for fresh sessions
    event_bus.register_handler(GetMealByIdQuery, GetMealByIdQueryHandler())

    event_bus.register_handler(
        GetDailyMacrosQuery, GetDailyMacrosQueryHandler(cache_service=None)
    )

    # Register user handlers
    # Note: Handlers now use UnitOfWork internally instead of receiving db in constructor
    save_user_handler = SaveUserOnboardingCommandHandler(cache_service=None)
    event_bus.register_handler(SaveUserOnboardingCommand, save_user_handler)

    # GetUserProfileQueryHandler resolves dependencies through the async UoW.
    event_bus.register_handler(GetUserProfileQuery, GetUserProfileQueryHandler())

    from src.app.commands.user import DeleteUserCommand
    from src.app.handlers.command_handlers.delete_user_command_handler import (
        DeleteUserCommandHandler,
    )

    event_bus.register_handler(DeleteUserCommand, DeleteUserCommandHandler())

    return event_bus


# Test Data Fixtures
@pytest.fixture
def sample_user(test_session) -> User:
    """Create a sample user for testing."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]  # Use shorter unique ID
    user = User(
        id=str(uuid.uuid4()),  # Generate unique ID for each test
        firebase_uid=f"test-fb-{unique_id}",
        email=f"test-{unique_id}@example.com",
        username=f"user-{unique_id}",
        password_hash="dummy_hash_for_test",
        is_active=True,  # Explicitly set to True for repository queries
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)  # Refresh to ensure user is loaded
    return user


@pytest.fixture
def sample_user_profile(test_session, sample_user) -> UserProfile:
    """Create a sample user profile for testing."""
    profile = UserProfile(
        user_id=sample_user.id,
        age=30,
        gender="male",
        height_cm=175,
        weight_kg=70,
        job_type="desk",
        training_days_per_week=4,
        training_minutes_per_session=60,
        fitness_goal="recomp",
        dietary_preferences=["vegetarian"],
        health_conditions=[],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    test_session.add(profile)
    test_session.commit()
    return profile


@pytest.fixture
def sample_meal_domain() -> Meal:
    """Create a sample meal domain object."""
    return Meal(
        meal_id="123e4567-e89b-12d3-a456-426614174001",
        user_id="123e4567-e89b-12d3-a456-426614174000",
        status=MealStatus.READY,
        created_at=datetime.now(),
        image=MealImage(
            image_id="123e4567-e89b-12d3-a456-426614174002",
            format="jpeg",
            size_bytes=100000,
            url="https://example.com/image.jpg",
        ),
        dish_name="Test Meal",
        nutrition=Nutrition(
            macros=Macros(
                protein=30.0,
                carbs=50.0,
                fat=20.0,
            ),
            food_items=[
                FoodItem(
                    id="sample-rice-id",
                    name="Rice",
                    quantity=150.0,
                    unit="g",
                    macros=Macros(
                        protein=5.0,
                        carbs=40.0,
                        fat=2.0,
                    ),
                ),
                FoodItem(
                    id="sample-chicken-id",
                    name="Chicken",
                    quantity=100.0,
                    unit="g",
                    macros=Macros(
                        protein=25.0,
                        carbs=10.0,
                        fat=18.0,
                    ),
                ),
            ],
            confidence_score=0.95,
        ),
        ready_at=datetime.now(),
    )


@pytest.fixture
def sample_meal_db(test_session, sample_meal_domain) -> MealORM:
    """Create a sample meal in the database."""
    # First create the meal image
    meal_image = meal_image_domain_to_orm(sample_meal_domain.image)
    test_session.add(meal_image)
    test_session.flush()

    # Create meal using mapper
    meal_model = meal_domain_to_orm(sample_meal_domain)
    test_session.add(meal_model)
    test_session.commit()
    return meal_model


@pytest.fixture
def sample_image_bytes() -> bytes:
    """Sample image bytes for testing."""
    # Simple 1x1 red pixel JPEG
    return bytes.fromhex(
        "ffd8ffe000104a46494600010101006000600000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c213232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232ffc00011080001000103012200021101031101ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100020102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00e2ffd9"
    )


@pytest.fixture
def sample_meal_with_nutrition(test_session, sample_user) -> Meal:
    """Create a sample meal with nutrition for editing tests."""
    import uuid

    # Create food items with IDs for editing
    food_items = [
        FoodItem(
            name="Grilled Chicken",
            quantity=150.0,
            unit="g",
            macros=Macros(
                protein=46.2,
                carbs=0.0,
                fat=5.4,
            ),
            id=str(uuid.uuid4()),
            fdc_id=171077,
            is_custom=False,
        ),
        FoodItem(
            name="Brown Rice",
            quantity=100.0,
            unit="g",
            macros=Macros(
                protein=2.6,
                carbs=22.0,
                fat=0.9,
            ),
            id=str(uuid.uuid4()),
            fdc_id=168880,
            is_custom=False,
        ),
        FoodItem(
            name="Mixed Vegetables",
            quantity=80.0,
            unit="g",
            macros=Macros(
                protein=1.5,
                carbs=7.0,
                fat=0.2,
            ),
            id=str(uuid.uuid4()),
            is_custom=True,
        ),
    ]

    meal = Meal(
        meal_id=str(uuid.uuid4()),
        user_id=sample_user.id,
        status=MealStatus.READY,
        created_at=datetime.now(),
        image=MealImage(
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=100000,
            url="https://example.com/meal.jpg",
        ),
        dish_name="Grilled Chicken with Rice and Vegetables",
        nutrition=Nutrition(
            macros=Macros(
                protein=50.3,
                carbs=29.0,
                fat=6.5,
            ),
            food_items=food_items,
            confidence_score=0.9,
        ),
        ready_at=datetime.now(),
        edit_count=0,
        is_manually_edited=False,
    )

    # Store in database
    meal_image_model = meal_image_domain_to_orm(meal.image)
    test_session.add(meal_image_model)
    test_session.flush()

    meal_model = meal_domain_to_orm(meal)
    test_session.add(meal_model)
    test_session.commit()

    return meal


@pytest.fixture
def sample_meal_processing(test_session, sample_user) -> Meal:
    """Create a sample meal in PROCESSING status for testing."""
    import uuid

    meal = Meal(
        meal_id=str(uuid.uuid4()),
        user_id=sample_user.id,
        status=MealStatus.PROCESSING,
        created_at=datetime.now(),
        image=MealImage(
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=100000,
            url="https://example.com/processing.jpg",
        ),
    )

    # Store in database
    meal_image_model = meal_image_domain_to_orm(meal.image)
    test_session.add(meal_image_model)
    test_session.flush()

    meal_model = meal_domain_to_orm(meal)
    test_session.add(meal_model)
    test_session.commit()

    return meal


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
