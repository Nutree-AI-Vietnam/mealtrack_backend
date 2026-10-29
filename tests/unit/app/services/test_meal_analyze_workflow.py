"""Tests for the sync-compatible meal analysis workflow seam."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime
from src.app.services.meal_analyze_workflow import MealAnalyzeWorkflow


@pytest.mark.asyncio
async def test_uploaded_workflow_runs_graph_then_delegates(monkeypatch):
    graph_calls = []
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph",
        lambda state: graph_calls.append(state),
    )
    expected_meal = object()
    legacy_handler = AsyncMock(return_value=expected_meal)
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"image-bytes",
        content_type="image/jpeg",
    )

    result = await MealAnalyzeWorkflow(graph_version="test-v2").run_uploaded(
        command, legacy_handler
    )

    assert result is expected_meal
    legacy_handler.assert_awaited_once_with(command)
    assert graph_calls == [
        {
            "scan_mode": "meal_scan",
            "user_id": command.user_id,
            "target_date": None,
            "graph_version": "test-v2",
        }
    ]


@pytest.mark.asyncio
async def test_scan_by_url_workflow_uses_food_label_state_then_delegates(monkeypatch):
    graph_calls = []
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph",
        lambda state: graph_calls.append(state),
    )
    expected_meal = object()
    legacy_handler = AsyncMock(return_value=expected_meal)
    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url="https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        public_id="mealtrack/abc",
        scan_mode="food_label",
    )

    result = await MealAnalyzeWorkflow().run_scan_by_url(command, legacy_handler)

    assert result is expected_meal
    legacy_handler.assert_awaited_once_with(command)
    assert graph_calls == [
        {
            "scan_mode": "food_label",
            "image_id": "abc",
            "user_id": command.user_id,
            "target_date": None,
            "graph_version": "v1",
        }
    ]


@pytest.mark.asyncio
async def test_workflow_validation_is_default_off(monkeypatch):
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph",
        lambda state: None,
    )
    meal = object()
    validation_service = AsyncMock()
    validation_service.validate_meal = AsyncMock(return_value=meal)
    legacy_handler = AsyncMock(return_value=meal)
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"image-bytes",
        content_type="image/jpeg",
    )

    result = await MealAnalyzeWorkflow(
        food_reference_validation_service=validation_service,
        fatsecret_validation_enabled=False,
    ).run_uploaded(command, legacy_handler)

    assert result is meal
    validation_service.validate_meal.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_runs_validation_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph",
        lambda state: None,
    )
    meal = object()
    validated_meal = object()
    validation_service = AsyncMock()
    validation_service.validate_meal = AsyncMock(
        return_value=SimpleNamespace(meal=validated_meal)
    )
    legacy_handler = AsyncMock(return_value=meal)
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"image-bytes",
        content_type="image/jpeg",
    )

    result = await MealAnalyzeWorkflow(
        food_reference_validation_service=validation_service,
        fatsecret_validation_enabled=True,
    ).run_uploaded(command, legacy_handler)

    assert result is validated_meal
    validation_service.validate_meal.assert_awaited_once_with(meal)


@pytest.mark.asyncio
async def test_uploaded_workflow_with_runtime_returns_graph_result_without_legacy(
    monkeypatch,
):
    expected_meal = object()
    graph_runner = AsyncMock(return_value={"result": expected_meal})
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph_async",
        graph_runner,
    )
    legacy_handler = AsyncMock()
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"image-bytes",
        content_type="image/jpeg",
    )
    runtime = MealAnalyzeRuntime(command=command)

    result = await MealAnalyzeWorkflow(graph_version="test-v2").run_uploaded(
        command,
        legacy_handler,
        runtime=runtime,
    )

    assert result is expected_meal
    legacy_handler.assert_not_awaited()
    graph_runner.assert_awaited_once_with(
        {
            "scan_mode": "meal_scan",
            "user_id": command.user_id,
            "target_date": None,
            "graph_version": "test-v2",
        },
        runtime,
    )


@pytest.mark.asyncio
async def test_scan_by_url_workflow_with_runtime_returns_graph_result_without_legacy(
    monkeypatch,
):
    expected_meal = object()
    graph_runner = AsyncMock(return_value={"result": expected_meal})
    monkeypatch.setattr(
        "src.app.services.meal_analyze_workflow.run_meal_analyze_graph_async",
        graph_runner,
    )
    legacy_handler = AsyncMock()
    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url="https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        public_id="mealtrack/abc",
        scan_mode="scanner",
    )
    runtime = MealAnalyzeRuntime(command=command)

    result = await MealAnalyzeWorkflow(graph_version="test-v2").run_scan_by_url(
        command,
        legacy_handler,
        runtime=runtime,
    )

    assert result is expected_meal
    legacy_handler.assert_not_awaited()
    graph_runner.assert_awaited_once_with(
        {
            "scan_mode": "meal_scan",
            "image_id": "abc",
            "user_id": command.user_id,
            "target_date": None,
            "graph_version": "test-v2",
        },
        runtime,
    )
