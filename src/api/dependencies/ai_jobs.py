"""FastAPI wiring for background AI jobs.

Job executors reuse the synchronous routes' command bus, chat orchestrator,
and response mapping, so they are composed here rather than in bootstrap.
"""

from __future__ import annotations

from typing import Any

from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.meals_route_helpers import parse_meal_text_response
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
    )


def _parse_text_result(app_response: Any) -> dict[str, Any]:
    # Stored as the job result, so the app reads the same shape as the
    # synchronous parse-text response.
    return parse_meal_text_response(app_response).model_dump(mode="json")
