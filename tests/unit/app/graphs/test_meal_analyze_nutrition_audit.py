"""One nutrition audit line per scan that persist_meal saves."""

import hashlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.graphs.meal_analyze import nodes
from src.app.graphs.meal_analyze.runtime import AcquiredImage, MealAnalyzeRuntime
from src.app.services.food_reference_validation_service import (
    FoodReferenceValidationService,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser

USER_ID = "11111111-2222-4333-8444-555555555555"
MEAL_ID = "00000000-0000-0000-0000-000000000301"
IMAGE_ID = "00000000-0000-0000-0000-000000000302"
IMAGE_URL = "https://images.example.test/private/scan.jpg"
FOOD_NAMES = ("Sesame", "Rice", "Sauce")
DISH_NAME = "Secret Bowl"


def _food(name: str, confidence: float, *, protein: float = 4) -> dict:
    return {
        "name": name,
        "quantity_g": 150,
        "confidence": confidence,
        "macros": {
            "protein_g": protein,
            "carbs_g": 45,
            "fat_g": 1,
            "fiber_g": 0,
            "sugar_g": 0,
        },
    }


def _vision_result(foods: list[dict], confidence: float) -> dict:
    return {
        "structured_data": {
            "is_food": True,
            "dish_name": DISH_NAME,
            "confidence": confidence,
            "foods": foods,
        }
    }


def _uow(*, save_error: Exception | None = None):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    if save_error is None:
        uow.meals.save = AsyncMock(side_effect=lambda meal: meal)
    else:
        uow.meals.save = AsyncMock(side_effect=save_error)
    uow.commit = AsyncMock()
    return uow


def _runtime(
    *,
    foods: list[dict],
    confidence: float,
    scan_mode: str = "scanner",
    validation_service=None,
    save_error: Exception | None = None,
) -> MealAnalyzeRuntime:
    parser = VisionResponseParser()
    vision = _vision_result(foods, confidence)
    return MealAnalyzeRuntime(
        command=ScanByUrlCommand(
            user_id=USER_ID,
            image_url=IMAGE_URL,
            public_id="mealtrack/private-scan",
            scan_mode=scan_mode,
        ),
        acquired_image=AcquiredImage(
            image_id=IMAGE_ID,
            image_url=IMAGE_URL,
            persisted_image_id=IMAGE_ID,
            persisted_image_url=IMAGE_URL,
            source_bytes=b"img",
            analysis_bytes=b"img",
            content_type="image/jpeg",
            content_kind="photo",
        ),
        vision_result=vision,
        nutrition=parser.parse_to_nutrition(vision),
        gpt_parser=parser,
        uow=_uow(save_error=save_error),
        event_publisher=MagicMock(),
        event_bus=MagicMock(),
        meal_id_factory=lambda: MEAL_ID,
        food_reference_validation_service=validation_service,
    )


def _audit_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "src.app.graphs.meal_analyze.nodes"
        and nodes.NUTRITION_AUDIT_EVENT in record.getMessage()
    ]


async def _persist(runtime: MealAnalyzeRuntime, state: dict | None = None):
    with patch.object(nodes, "publish_meal_event", new=AsyncMock()):
        return await nodes.persist_meal(state or {}, runtime)


@pytest.mark.asyncio
async def test_successful_scan_emits_one_audit_line_without_pii(caplog):
    caplog.set_level(logging.INFO)
    foods = [
        _food("Sesame", 0.9),
        _food("Rice", 0.6),
        _food("Sauce", 0.2),
    ]
    runtime = _runtime(foods=foods, confidence=0.4)

    await _persist(runtime)

    messages = _audit_messages(caplog)
    assert len(messages) == 1
    message = messages[0]
    assert "\n" not in message
    payload = json.loads(message)
    assert payload["event"] == "meal_scan.nutrition_audit"
    assert payload["meal_id"] == MEAL_ID
    assert payload["user_id_hash"] == hashlib.sha256(USER_ID.encode()).hexdigest()
    assert payload["scan_source"] == "scanner"
    assert payload["confidence_buckets"] == {"high": 1, "review": 1, "low": 1}
    assert payload["meal_confidence_bucket"] == "low"
    assert [item["confidence_bucket"] for item in payload["item_sources"]] == [
        "high",
        "review",
        "low",
    ]
    assert {item["source"] for item in payload["item_sources"]} == {"vision"}
    assert payload["validation"] == {
        "outcome": "skipped",
        "reason_code": "not_run",
        "error_type": None,
        "items": [],
    }
    assert USER_ID not in message
    assert IMAGE_URL not in message
    assert DISH_NAME not in message
    for name in FOOD_NAMES:
        assert name not in message


@pytest.mark.asyncio
async def test_food_label_scan_audits_label_source(caplog):
    caplog.set_level(logging.INFO)
    runtime = _runtime(
        foods=[_food("Sesame", 0.95)],
        confidence=0.9,
        scan_mode="food_label",
    )

    await _persist(runtime)

    messages = _audit_messages(caplog)
    assert len(messages) == 1
    payload = json.loads(messages[0])
    assert payload["scan_source"] == "food_label"
    assert payload["item_sources"][0]["source"] == "food_label"
    assert payload["validation"]["outcome"] == "skipped"
    assert "Sesame" not in messages[0]
    assert USER_ID not in messages[0]


@pytest.mark.asyncio
async def test_validation_report_is_included_in_the_single_audit_line(caplog):
    caplog.set_level(logging.INFO)
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        return_value=[{"food_id": "rice", "description": "Rice", "source": "fatsecret"}]
    )
    provider.get_food_details = AsyncMock(
        return_value={
            "source": "fatsecret",
            "protein_100g": 2.7,
            "carbs_100g": 30,
            "fat_100g": 0.7,
        }
    )
    service = FoodReferenceValidationService(nutrition_reference_provider=provider)
    runtime = _runtime(
        foods=[_food("Rice", 0.91)],
        confidence=0.88,
        validation_service=service,
    )

    await _persist(runtime, {"reference_validation_pending": True})

    messages = _audit_messages(caplog)
    assert len(messages) == 1
    payload = json.loads(messages[0])
    assert payload["validation"]["outcome"] == "match"
    assert payload["validation"]["error_type"] is None
    assert payload["validation"]["items"][0]["outcome"] == "match"
    assert payload["validation"]["items"][0]["source"] == "fatsecret"
    assert payload["validation"]["items"][0]["reason_code"] == "matched"
    assert payload["item_sources"][0]["source"] == "fatsecret"
    assert payload["item_sources"][0]["confidence_bucket"] == "high"
    assert "Rice" not in messages[0]
    assert USER_ID not in messages[0]
    provider.search_food_candidates.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_save_emits_no_audit_line(caplog):
    caplog.set_level(logging.INFO)
    runtime = _runtime(
        foods=[_food("Rice", 0.9)],
        confidence=0.9,
        save_error=RuntimeError("db down"),
    )

    with pytest.raises(RuntimeError, match="db down"):
        await _persist(runtime)

    assert _audit_messages(caplog) == []
