"""FastAPI wiring for background AI jobs.

Job executors reuse the synchronous routes' command bus, chat orchestrator,
and response mapping, so they are composed here rather than in bootstrap.
"""

from __future__ import annotations

from typing import Any

from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.meals_route_helpers import parse_meal_text_response
from src.api.schemas.response.describe_log_responses import (
    DescribeAnyResponse,
    DescribeHydrationResponse,
    DescribeMovementResponse,
)
from src.api.schemas.response.recipe_import_responses import ImportedRecipeResponse
from src.app.services.ai_jobs.executors import AiJobExecutor
from src.bootstrap.ai_jobs import (
    build_ai_job_executors,
    get_ai_job_service,
    get_chat_job_runner,
    get_push_token_service,
    shutdown_ai_jobs,
    start_ai_jobs,
)
from src.domain.model.ai_jobs import AiJobType

__all__ = [
    "get_ai_job_service",
    "get_chat_job_runner",
    "get_push_token_service",
    "shutdown_ai_jobs",
    "start_ai_jobs_worker",
]


async def start_ai_jobs_worker() -> bool:
    """Start this process's worker when ``AI_JOBS_WORKER_ENABLED`` is set."""
    return await start_ai_jobs(_build_executors)


def _build_executors() -> dict[AiJobType, AiJobExecutor]:
    bus = get_configured_event_bus()
    return build_ai_job_executors(
        send=bus.send,
        orchestrator_factory=get_chat_turn_orchestrator,
        parse_text_result=_parse_text_result,
        recipe_import_result=_recipe_import_result,
        describe_results={
            AiJobType.DESCRIBE_LOG: _describe_result(DescribeAnyResponse),
            AiJobType.DESCRIBE_MOVEMENT: _describe_result(DescribeMovementResponse),
            AiJobType.DESCRIBE_HYDRATION: _describe_result(DescribeHydrationResponse),
        },
    )


def _recipe_import_result(recipe: Any) -> dict[str, Any]:
    # The same draft POST /v1/recipe-imports/extract returns.
    return ImportedRecipeResponse.from_domain(recipe).model_dump(mode="json")


def _describe_result(model: Any) -> Any:
    # The reply the Ghi nhanh route returns, so the app reads one shape either
    # way. The + panel's saved meals carry no meal detail here; the app reloads
    # the day for a food item without one, as it does when the route's detail
    # read fails.
    def to_result(reply: Any) -> dict[str, Any]:
        return model.model_validate(reply).model_dump(mode="json")

    return to_result


def _parse_text_result(app_response: Any) -> dict[str, Any]:
    # Stored as the job result, so the app reads the same shape as the
    # synchronous parse-text response.
    return parse_meal_text_response(app_response).model_dump(mode="json")
