"""Exercise consent, immutable evidence, review, and withdrawal with real SQL."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, delete, update
from sqlalchemy.orm import Session

_spec = importlib.util.spec_from_file_location(
    "review_scan_feedback",
    Path(__file__).resolve().parents[3] / "scripts/development/review_scan_feedback.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
export_records, review = _module.export_records, _module.review
validate_records = _module.validate_records
from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.api.mappers.meal_mapper import MealMapper
from src.api.routes.v1.meals_scan_feedback import ScanFeedbackRequest
from src.app.services.scan_feedback_service import ScanFeedbackService
from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.meal.scan_feedback import meal_snapshot
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.meal.scan_feedback import ScanFeedbackORM
from src.infra.repositories.scan_feedback_repository import ScanFeedbackRepository


@pytest.fixture
def meal():
    now = utc_now()
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=now,
        ready_at=now,
        source="scanner",
        dish_name="Rice bowl",
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=100),
        nutrition=Nutrition(
            macros=Macros(1, 20, 2),
            food_items=[
                FoodItem(
                    id=str(uuid4()),
                    name="Sesame",
                    quantity=0.5,
                    unit="g",
                    macros=Macros(1, 2, 3),
                    confidence=0.53,
                ),
            ],
            confidence_score=0.8,
        ),
        raw_gpt_json='{"_scan_provenance":{"model":"vision-test","prompt_sha256":"abc"}}',
    )


@pytest.fixture
def db(meal):
    # Minimal parent tables keep this portable; actual feedback table and SQL are used.
    metadata = MetaData()
    users = Table("users", metadata, Column("id", String, primary_key=True))
    meals = Table(
        "meal",
        metadata,
        Column("meal_id", String, primary_key=True),
        Column("status", String),
        Column("image_id", String),
    )
    ScanFeedbackORM.__table__.to_metadata(metadata)
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    with Session(engine) as session:
        session.execute(users.insert().values(id=meal.user_id))
        session.execute(
            meals.insert().values(
                meal_id=meal.meal_id, status="READY", image_id=meal.image.image_id
            )
        )
        yield session, meals
    engine.dispose()


def repository(session):
    return ScanFeedbackRepository(
        SimpleNamespace(
            get=AsyncMock(side_effect=session.get),
            add=session.add,
            flush=AsyncMock(side_effect=session.flush),
        )
    )


@pytest.mark.asyncio
async def test_baseline_is_immutable_and_not_shared(db, meal):
    session, _ = db
    repo = repository(session)
    await repo.capture_baseline(meal)
    row = session.get(ScanFeedbackORM, meal.meal_id)
    assert row.original["foods"][0]["name"] == "Sesame"
    assert row.original["provenance"]["model"] == "vision-test"
    meal.nutrition.food_items[0].name = "Chia"
    await repo.capture_baseline(meal)
    assert row.original["foods"][0]["name"] == "Sesame"
    assert row.consent is False
    assert list(export_records(session, 10)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attribute,value",
    [("source", "manual"), ("status", MealStatus.ANALYZING), ("edit_count", 1)],
)
async def test_only_original_completed_scan_gets_baseline(db, meal, attribute, value):
    session, _ = db
    setattr(meal, attribute, value)
    await repository(session).capture_baseline(meal)
    assert session.get(ScanFeedbackORM, meal.meal_id) is None


@pytest.mark.asyncio
async def test_reviewed_feedback_idempotency_re_review_and_withdrawal(db, meal):
    session, _ = db
    repo = repository(session)
    await repo.capture_baseline(meal)
    meal.nutrition.food_items[0].name = "Chia"
    meal.edit_count = 1
    corrected = meal_snapshot(meal)
    assert await repo.submit(meal.meal_id, corrected, "identity") == "pending"
    assert list(export_records(session, 10)) == []
    row = session.get(ScanFeedbackORM, meal.meal_id)
    with pytest.raises(ValueError, match="changed"):
        review(
            session, meal.meal_id, "stale-hash", "approved", "reviewer", "Checked photo"
        )
    review(
        session,
        meal.meal_id,
        row.submission_hash,
        "approved",
        "reviewer",
        "Checked photo",
    )
    exported = list(export_records(session, 10))
    assert len(exported) == 1
    validate_records(session, exported)
    assert exported[0]["original"]["foods"][0]["name"] == "Sesame"
    assert exported[0]["corrected"]["foods"][0]["name"] == "Chia"
    assert "user_id" not in exported[0]
    assert await repo.submit(meal.meal_id, corrected, "identity") == "approved"
    meal.nutrition.food_items[0].quantity = 1
    assert await repo.submit(meal.meal_id, meal_snapshot(meal), "mixed") == "pending"
    assert row.reviewed_at is None and row.reviewer is None
    assert list(export_records(session, 10)) == []
    review(
        session, meal.meal_id, row.submission_hash, "approved", "reviewer", "Rechecked"
    )
    await repo.withdraw(meal.meal_id)
    await repo.withdraw(meal.meal_id)
    assert row.corrected is None and row.consent is False
    with pytest.raises(ValueError, match="stale"):
        validate_records(session, exported)
    assert list(export_records(session, 10)) == []
    with pytest.raises(ValueError):
        review(session, meal.meal_id, "old", "approved", "reviewer", "Must fail")


@pytest.mark.asyncio
async def test_image_replacement_and_deletion_exclude_evidence(db, meal):
    session, meals = db
    repo = repository(session)
    await repo.capture_baseline(meal)
    await repo.submit(meal.meal_id, meal_snapshot(meal), "confirmation")
    row = session.get(ScanFeedbackORM, meal.meal_id)
    review(
        session,
        meal.meal_id,
        row.submission_hash,
        "approved",
        "reviewer",
        "Confirmed photo",
    )
    session.execute(update(meals).values(image_id=str(uuid4())))
    assert list(export_records(session, 10)) == []
    meal.image = replace(meal.image, image_id=str(uuid4()))
    with pytest.raises(ValueError, match="photo has changed"):
        await repo.submit(meal.meal_id, meal_snapshot(meal), "confirmation")
    session.execute(delete(meals))
    session.expire_all()
    assert session.get(ScanFeedbackORM, meal.meal_id) is None
    assert list(export_records(session, 10)) == []


@pytest.mark.asyncio
async def test_soft_deleted_meal_excluded(db, meal):
    session, meals = db
    repo = repository(session)
    await repo.capture_baseline(meal)
    await repo.submit(meal.meal_id, meal_snapshot(meal), "confirmation")
    row = session.get(ScanFeedbackORM, meal.meal_id)
    session.execute(update(meals).values(status="INACTIVE"))
    with pytest.raises(ValueError):
        review(
            session,
            meal.meal_id,
            row.submission_hash,
            "approved",
            "reviewer",
            "Must fail",
        )


@pytest.mark.asyncio
async def test_legacy_scan_cannot_create_fake_baseline(db, meal):
    with pytest.raises(ValueError, match="no original"):
        await repository(db[0]).submit(
            meal.meal_id, meal_snapshot(meal), "confirmation"
        )


def service_for(meal):
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    uow.meals.find_by_id_for_update.return_value = meal
    uow.scan_feedback.submit.return_value = "pending"
    return ScanFeedbackService(lambda: uow), uow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,error",
    [
        ({"user_id": "someone-else"}, ResourceNotFoundException),
        ({"consent": False}, ValidationException),
        ({"expected_edit_count": 9}, ConflictException),
        ({"correction_kind": "invented"}, ValidationException),
    ],
)
async def test_invalid_or_stale_submission_never_writes(meal, change, error):
    service, uow = service_for(meal)
    args = {
        "meal_id": meal.meal_id,
        "user_id": meal.user_id,
        "expected_edit_count": 0,
        "correction_kind": "confirmation",
        "consent": True,
    }
    with pytest.raises(error):
        await service.submit(**(args | change))
    uow.scan_feedback.submit.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_snapshots_saved_meal_and_owner_controls_withdrawal(meal):
    service, uow = service_for(meal)
    result = await service.submit(
        meal_id=meal.meal_id,
        user_id=meal.user_id,
        expected_edit_count=0,
        correction_kind="confirmation",
        consent=True,
    )
    assert result == {"status": "pending"}
    uow.scan_feedback.submit.assert_awaited_once_with(
        meal.meal_id, meal_snapshot(meal), "confirmation"
    )
    uow.commit.assert_awaited_once()
    with pytest.raises(ResourceNotFoundException):
        await service.withdraw(meal_id=meal.meal_id, user_id="someone-else")
    uow.scan_feedback.withdraw.assert_not_awaited()
    await service.withdraw(meal_id=meal.meal_id, user_id=meal.user_id)
    uow.scan_feedback.withdraw.assert_awaited_once_with(meal.meal_id)


def test_feedback_request_rejects_client_labels_and_implicit_consent():
    from pydantic import ValidationError

    payload = {"expected_edit_count": 0, "correction_kind": "identity", "consent": True}
    for extra in (
        {"original": {}},
        {"corrected": {}},
        {"consent": "true"},
        {"expected_edit_count": -1},
    ):
        with pytest.raises(ValidationError):
            ScanFeedbackRequest(**(payload | extra))


def test_confidence_and_revision_reach_api_without_validating_user_edits(meal):
    response = MealMapper.to_detailed_response(meal)
    assert response.food_items[0].confidence == 0.53
    assert response.edit_count == 0
    meal.edit_count = 2
    meal.is_manually_edited = True
    response = MealMapper.to_detailed_response(meal)
    assert response.edit_count == 2 and response.is_manually_edited
    assert response.food_items[0].confidence is None


def test_snapshot_preserves_explicit_nutrition_corrections(meal):
    from src.domain.model.nutrition.nutrition import NutritionOverride

    meal.nutrition.food_items[0].nutrition_override = NutritionOverride(25, 2, 3, 1)
    meal.nutrition.nutrition_override = NutritionOverride(300, 10, 50, 7)
    snapshot = meal_snapshot(meal)
    assert snapshot["foods"][0]["nutrition_override"]["calories"] == 25
    assert snapshot["nutrition_override"]["calories"] == 300
