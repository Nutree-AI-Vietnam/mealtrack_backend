"""Interactive OpenAI planner admission, deadline and one transient retry."""

import asyncio
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic

from pydantic import ValidationError

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.observability import increment_metric
from src.planner_observability import planner_phase
from src.planner_request_policy import (
    consume_planner_retry,
    remaining_budget,
    timeout_until,
)


class PlannerGenerationPolicy:
    """One per-process allocation; all attempts share its provider deadline."""

    def __init__(self):
        capacity = int(os.getenv("MEAL_PLAN_AI_CAPACITY_PER_PROCESS", "2"))
        self._admission_wait = float(
            os.getenv("MEAL_PLAN_AI_ADMISSION_WAIT_SECONDS", "1")
        )
        if capacity < 1 or not 0 < self._admission_wait <= 25:
            raise ValueError("Planner AI capacity/wait configuration is invalid")
        self._slots = asyncio.Semaphore(capacity)

    async def generate(self, *, provider, model, circuit_breaker, **request):
        if provider is None:
            raise AIUnavailableError("OpenAI planner provider is unavailable")
        if not circuit_breaker.filter_available([model]):
            raise AIUnavailableError("OpenAI planner circuit is open")
        deadline = monotonic() + remaining_budget(25)
        acquired = False
        try:
            with planner_phase("admission"):
                await asyncio.wait_for(
                    self._slots.acquire(),
                    timeout=min(self._admission_wait, max(0, deadline - monotonic())),
                )
            acquired = True
            # This outer timeout includes admission, retry waits and both attempts.
            async with timeout_until(deadline):
                for attempt in range(2):
                    budget = deadline - monotonic()
                    if budget <= 0:
                        raise TimeoutError("Planner provider deadline expired")
                    attributes = {
                        "ai_provider": "openai",
                        "ai_model": model,
                        "ai_purpose": "meal_plan_adjustment",
                        "attempt_count": attempt + 1,
                    }
                    try:
                        increment_metric(
                            "ai.planner.attempt.count", attributes=attributes
                        )
                    except Exception:
                        pass
                    try:
                        with planner_phase("ai_attempt"):
                            result = await provider.generate(
                                model=model,
                                purpose_hint="meal_plan_adjustment",
                                request_timeout_seconds=budget,
                                **request,
                            )
                        circuit_breaker.record_success(model)
                        return result
                    except Exception as error:
                        code = provider.extract_error_code(error)
                        if circuit_breaker.should_trip(code):
                            circuit_breaker.record_failure(model)
                        transient = code in {429, "connection", "timeout"} or (
                            isinstance(code, int) and 500 <= code < 600
                        )
                        if isinstance(
                            error,
                            (ValueError, ValidationError, AIOutputValidationError),
                        ):
                            transient = False
                        if not transient or attempt == 1 or not consume_planner_retry():
                            raise AIUnavailableError(
                                "OpenAI planner request failed",
                                attempted_models=[model],
                                last_error=type(error).__name__,
                            ) from error
                        delay = _retry_delay(error)
                        if delay >= deadline - monotonic():
                            raise TimeoutError(
                                "Planner retry exceeds provider deadline"
                            ) from error
                        await asyncio.sleep(delay)
        finally:
            if acquired:
                self._slots.release()


def _retry_delay(error: Exception) -> float:
    """Honor Retry-After; keep all waiting inside the aggregate budget."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    try:
        retry_after = float(headers.get("retry-after", "0.1"))
    except (TypeError, ValueError):
        try:
            retry_after = (
                parsedate_to_datetime(headers["retry-after"]) - datetime.now(UTC)
            ).total_seconds()
        except (TypeError, ValueError, KeyError):
            retry_after = 0.1
    return max(0.0, retry_after)
