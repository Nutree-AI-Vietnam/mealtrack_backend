from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infra.adapters.meal_generation_service import MealGenerationService


@pytest.fixture
def mock_ai_manager():
    manager = Mock()
    manager.generate = AsyncMock(return_value={"meal": "test"})
    return manager


@pytest.fixture
def service(mock_ai_manager):
    with patch("src.infra.adapters.meal_generation_service.AIModelManager") as mock_cls:
        mock_cls.get_instance.return_value = mock_ai_manager
        return MealGenerationService()


@pytest.mark.asyncio
async def test_generate_meal_plan_uses_ai_manager(service, mock_ai_manager):
    result = await service.generate_meal_plan_async(
        prompt="test prompt",
        system_message="test system",
        model_purpose="meal_names",
    )

    assert result == {"meal": "test"}


@pytest.mark.asyncio
async def test_model_purpose_maps_to_enum(service, mock_ai_manager):
    """recipe purpose maps to RECIPE enum (collapsed from PRIMARY/SECONDARY)."""
    await service.generate_meal_plan_async(
        prompt="test",
        system_message="system",
        model_purpose="recipe",
    )

    call_kwargs = mock_ai_manager.generate.call_args[1]
    assert call_kwargs["purpose"].value == "recipe"


@pytest.mark.asyncio
async def test_discovery_model_purpose_maps_to_enum(service, mock_ai_manager):
    await service.generate_meal_plan_async(
        prompt="test",
        system_message="system",
        model_purpose="discovery",
    )

    call_kwargs = mock_ai_manager.generate.call_args[1]
    assert call_kwargs["purpose"].value == "discovery"


@pytest.mark.asyncio
async def test_a_request_timeout_reaches_the_provider_only_when_asked_for(
    service, mock_ai_manager
):
    await service.generate_meal_plan_async(
        "prompt", "system", "json", model_purpose="recipe", request_timeout_seconds=90
    )
    assert mock_ai_manager.generate.await_args.kwargs["request_timeout_seconds"] == 90

    await service.generate_meal_plan_async("prompt", "system", "json")
    assert "request_timeout_seconds" not in mock_ai_manager.generate.await_args.kwargs
