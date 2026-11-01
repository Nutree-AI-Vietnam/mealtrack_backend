"""Saving a reviewed draft, the import guard and the background job executor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_recipe_import import FakeNutrition

from src.api.exceptions import (
    ExternalServiceException,
    TooManyRequestsException,
    handle_exception,
)
from src.app.commands.recipe_import import (
    ImportRecipeCommand,
    SaveImportedRecipeCommand,
)
from src.app.commands.saved_suggestion import SaveSuggestionCommand
from src.app.handlers.command_handlers.recipe_import import (
    ImportRecipeCommandHandler,
    SaveImportedRecipeCommandHandler,
)
from src.app.services.ai_jobs.error_mapping import map_job_exception
from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.app.services.ai_jobs.recipe_import_executor import RecipeImportJobExecutor
from src.app.services.recipe_import.recipe_import_guard import (
    RecipeImportGuard,
    recipe_import_quota_key,
)
from src.app.services.recipe_import.recipe_nutrition_pricer import RecipeNutritionPricer
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.ai_jobs import AiJobClaim, AiJobErrorKind, AiJobType
from src.infra.database.models.saved_suggestion import SavedSuggestionModel
from src.infra.repositories.saved_suggestion_normalization import (
    apply_normalized_saved_suggestion_fields,
    project_saved_suggestion_data,
)

DRAFT: dict[str, Any] = {
    "name": "Cánh gà chiên nước mắm",
    "english_name": "Fish sauce chicken wings",
    "description": "Giòn, mặn ngọt",
    "emoji": "🍗",
    "servings": 2,
    "prep_time_minutes": 15,
    "cook_time_minutes": 12,
    "image_url": "https://p16.tiktokcdn.com/thumb.jpg",
    "ingredients": [
        {
            "name": "cánh gà",
            "english_name": "chicken wing, raw",
            "quantity": 500,
            "unit": "g",
        },
        {
            "name": "nước mắm",
            "english_name": "fish sauce",
            "quantity": 2,
            "unit": "tbsp",
        },
        {"name": "muối", "english_name": "salt", "quantity": None, "unit": None},
    ],
    "steps": [
        {"instruction": "Ướp gà", "duration_minutes": 15},
        {"instruction": "Chiên"},
    ],
    "source": {
        "type": "url",
        "platform": "tiktok",
        "url": "https://www.tiktok.com/@chef/video/1",
        "title": "Gà chiên",
    },
}


class _Uow:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.saved_suggestions = AsyncMock()
        self.saved_suggestions.find_by_user_and_suggestion.return_value = existing

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _save_handler(
    send: AsyncMock, *, uow: _Uow | None = None, archive: Any = None
) -> SaveImportedRecipeCommandHandler:
    uow = uow or _Uow()
    return SaveImportedRecipeCommandHandler(
        pricer=RecipeNutritionPricer(FakeNutrition()),
        send=send,
        uow_factory=lambda: uow,
        image_archive=archive,
    )


def _command(**overrides: Any) -> SaveImportedRecipeCommand:
    recipe = {**DRAFT, **overrides}
    return SaveImportedRecipeCommand(
        user_id="u1",
        import_id="imp123456789",
        language="vi",
        recipe=recipe,
        meal_type="dinner",
    )


@pytest.mark.asyncio
async def test_save_reprices_archives_the_image_and_saves_a_suggestion():
    send = AsyncMock(
        side_effect=lambda command: {
            "id": "row",
            "suggestion_data": command.suggestion_data,
        }
    )
    archive = AsyncMock()
    archive.archive.return_value = "https://imagedelivery.net/x/stored"

    result = await _save_handler(send, archive=archive).handle(_command())

    command = send.await_args.args[0]
    assert isinstance(command, SaveSuggestionCommand)
    assert (command.user_id, command.suggestion_id, command.meal_type) == (
        "u1",
        "recipe_import_imp123456789",
        "dinner",
    )
    data = command.suggestion_data
    assert data["meal_name"] == "Cánh gà chiên nước mắm"
    assert data["servings"] == 2
    assert isinstance(data["macros"]["calories"], int)
    assert data["macros"]["protein"] == pytest.approx((90 + 1.8) / 2, abs=0.2)
    assert data["image_url"] == "https://imagedelivery.net/x/stored"
    assert data["recipe_steps"] == [
        {"step": 1, "instruction": "Ướp gà", "duration_minutes": 15},
        {"step": 2, "instruction": "Chiên", "duration_minutes": None},
    ]
    assert data["prep_time_minutes"] == 27
    assert data["recipe_import"]["platform"] == "tiktok"
    assert data["recipe_import"]["warnings"] == ["nutrition_partial"]
    assert result["id"] == "row"


@pytest.mark.asyncio
async def test_saved_snapshot_projects_to_the_shape_the_app_parses():
    send = AsyncMock(side_effect=lambda command: command.suggestion_data)
    data = await _save_handler(send).handle(_command(image_url=None))
    model = SavedSuggestionModel(suggestion_data=data, saved_at=datetime.now(UTC))
    apply_normalized_saved_suggestion_fields(model, data)

    projected = project_saved_suggestion_data(model)

    for item in projected["ingredients"]:
        assert isinstance(item["name"], str)
        assert isinstance(item["amount"], float)
        assert isinstance(item["unit"], str)
    assert projected["ingredients"][0]["english_name"] == "chicken wing, raw"
    assert (
        projected["ingredients"][2] | {"amount": 0.0, "unit": ""}
        == projected["ingredients"][2]
    )
    assert {
        "id",
        "meal_name",
        "description",
        "macros",
        "recipe_steps",
        "prep_time_minutes",
        "confidence_score",
    } <= set(projected)


@pytest.mark.asyncio
async def test_saving_the_same_import_again_returns_the_first_save():
    send = AsyncMock()
    existing = {"id": "row", "suggestion_id": "recipe_import_imp123456789"}

    result = await _save_handler(send, uow=_Uow(existing)).handle(_command())

    assert result == existing
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_archive_keeps_the_original_image_link():
    send = AsyncMock(side_effect=lambda command: command.suggestion_data)
    archive = AsyncMock()
    archive.archive.return_value = None

    data = await _save_handler(send, archive=archive).handle(_command())

    assert data["image_url"] == "https://p16.tiktokcdn.com/thumb.jpg"


@pytest.mark.asyncio
async def test_save_needs_a_name_and_an_ingredient():
    with pytest.raises(RecipeImportError) as caught:
        await _save_handler(AsyncMock()).handle(_command(ingredients=[]))

    assert caught.value.failure == RecipeImportFailure.INVALID_SOURCE


def test_import_command_takes_exactly_one_source():
    with pytest.raises(RecipeImportError):
        ImportRecipeCommand(user_id="u", language="en")
    with pytest.raises(RecipeImportError):
        ImportRecipeCommand(user_id="u", language="en", url="https://a.com", text="x")


@pytest.mark.asyncio
async def test_import_handler_passes_the_request_through():
    service = AsyncMock()
    service.import_recipe.return_value = "draft"

    result = await ImportRecipeCommandHandler(service).handle(
        ImportRecipeCommand(user_id="u", language="vi", url="https://a.com/x")
    )

    request = service.import_recipe.await_args.args[0]
    assert (result, request.url, request.language) == ("draft", "https://a.com/x", "vi")


class TestGuard:
    @pytest.mark.asyncio
    async def test_counts_each_import_against_the_daily_limit(self):
        counter = AsyncMock(side_effect=[1, 2, 3])
        guard = RecipeImportGuard(
            enabled=True, daily_limit=2, increment_counter=counter
        )

        await guard.admit("u1")
        await guard.admit("u1")
        with pytest.raises(TooManyRequestsException) as caught:
            await guard.admit("u1")

        assert caught.value.error_code == "RECIPE_IMPORT_DAILY_LIMIT"
        key = counter.await_args.args[0]
        assert key.startswith("recipe_import:quota:u1:")

    @pytest.mark.asyncio
    async def test_switched_off_imports_fail_before_counting(self):
        counter = AsyncMock()
        guard = RecipeImportGuard(
            enabled=False, daily_limit=5, increment_counter=counter
        )

        with pytest.raises(ExternalServiceException) as caught:
            await guard.admit("u1")

        assert caught.value.error_code == "RECIPE_IMPORT_DISABLED"
        counter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redis_outage_allows_the_import(self):
        guard = RecipeImportGuard(
            enabled=True, daily_limit=1, increment_counter=AsyncMock(return_value=None)
        )

        await guard.admit("u1")

    def test_quota_key(self):
        assert (
            recipe_import_quota_key("u", datetime(2026, 11, 1).date())
            == "recipe_import:quota:u:2026-11-01"
        )


def _claim(payload: dict[str, Any]) -> AiJobClaim:
    return AiJobClaim(
        id="job",
        user_id="u1",
        job_type=AiJobType.RECIPE_IMPORT,
        language="vi",
        request_payload=payload,
        claim_token="t",
        attempts=1,
        max_attempts=4,
        lease_expires_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_job_executor_imports_and_returns_the_draft():
    send = AsyncMock(return_value="draft")
    to_result = lambda recipe: {"name": recipe}  # noqa: E731

    result = await RecipeImportJobExecutor(send=send, to_result=to_result).execute(
        _claim(
            {"url": "https://a.com/x", "text": None, "image_url": None, "note": None}
        ),
        AiJobInterrupt(),
    )

    command = send.await_args.args[0]
    assert isinstance(command, ImportRecipeCommand)
    assert (command.user_id, command.language, command.url) == (
        "u1",
        "vi",
        "https://a.com/x",
    )
    assert result == {"recipe": {"name": "draft"}}


@pytest.mark.parametrize(
    ("failure", "kind", "retryable"),
    [
        (RecipeImportFailure.INVALID_SOURCE, AiJobErrorKind.PERMANENT, False),
        (RecipeImportFailure.URL_NOT_ALLOWED, AiJobErrorKind.PERMANENT, False),
        (RecipeImportFailure.SOURCE_REJECTED, AiJobErrorKind.PERMANENT, False),
        (RecipeImportFailure.NOT_A_RECIPE, AiJobErrorKind.PERMANENT, True),
        (RecipeImportFailure.VIDEO_UNAVAILABLE, AiJobErrorKind.PERMANENT, True),
        (RecipeImportFailure.SOURCE_UNREACHABLE, AiJobErrorKind.TRANSIENT, True),
    ],
)
def test_import_failures_map_to_job_errors(failure, kind, retryable):
    error = map_job_exception(RecipeImportError(failure, "x"))

    assert (error.code, error.kind, error.retryable) == (failure.value, kind, retryable)


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (RecipeImportFailure.INVALID_SOURCE, 400),
        (RecipeImportFailure.URL_NOT_ALLOWED, 400),
        (RecipeImportFailure.SOURCE_UNREACHABLE, 503),
        (RecipeImportFailure.NOT_A_RECIPE, 422),
    ],
)
def test_import_failures_map_to_http_statuses(failure, status):
    http = handle_exception(RecipeImportError(failure, "msg", details={"a": 1}))

    assert http.status_code == status
    assert http.detail == {
        "error_code": failure.value,
        "message": "msg",
        "details": {"a": 1},
    }
