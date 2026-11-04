"""Model call and number hygiene shared by the Ghi nhanh describe handlers.

The deadline covers generation only, never the write, so a slow model can't
turn a committed save into a 503 that the app would retry.
"""

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.ai_output_validation_service import (
    build_validation_retry_prompt,
    validate_ai_output,
)

DESCRIBE_MAX_ITEMS = 8
DESCRIBE_NAME_MAX_CHARS = 60
DESCRIBE_MAX_TOKENS = 1500
DESCRIBE_MAX_ATTEMPTS = 2


def sentence_block(sentence: str) -> str:
    """Delimit the person's sentence; the sanitiser removed any angle brackets."""
    return f"<sentence>\n{sentence}\n</sentence>"


async def parse_describe_sentence(
    generation_service: MealGenerationServicePort,
    *,
    sentence: str,
    system_prompt: str,
    schema: type[BaseModel],
    purpose: str,
    timeout_seconds: float,
    model_purpose: str = "parse_text",
    context: str = "",
    max_tokens: int = DESCRIBE_MAX_TOKENS,
) -> dict[str, Any]:
    """Return the validated model reply, after at most one correction.

    ``context`` is a data block sent ahead of the sentence (the + panel's
    list of the person's supplements); the system prompt stays the same for
    everyone, so it can be cached.
    """
    try:
        return await asyncio.wait_for(
            _generate(
                generation_service,
                _Request(sentence, system_prompt, schema, purpose),
                model_purpose=model_purpose,
                context=context,
                max_tokens=max_tokens,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise AIUnavailableError(
            f"{purpose} AI deadline exceeded", last_error="timeout"
        ) from exc


@dataclass(frozen=True)
class _Request:
    sentence: str
    system_prompt: str
    schema: type[BaseModel]
    purpose: str


async def _generate(
    generation_service: MealGenerationServicePort,
    request: _Request,
    *,
    model_purpose: str,
    context: str,
    max_tokens: int,
) -> dict[str, Any]:
    first_prompt = f"{context}{sentence_block(request.sentence)}"
    prompt = first_prompt
    for attempt in range(1, DESCRIBE_MAX_ATTEMPTS + 1):
        try:
            raw = await generation_service.generate_meal_plan_async(
                prompt=prompt,
                system_message=request.system_prompt,
                response_type="json",
                max_tokens=max_tokens,
                schema=request.schema,
                model_purpose=model_purpose,
                thinking_budget=0,
            )
            return validate_ai_output(
                _payload(raw, request.purpose),
                schema=request.schema,
                purpose=request.purpose,
                attempt_count=attempt,
            )
        except ConnectionError as exc:
            raise AIUnavailableError(
                f"Upstream connection failed during {request.purpose}",
                last_error=type(exc).__name__,
            ) from exc
        except AIOutputValidationError as exc:
            if attempt >= DESCRIBE_MAX_ATTEMPTS:
                raise
            prompt = build_validation_retry_prompt(first_prompt, exc)
    raise RuntimeError(f"{request.purpose} produced no reply")


def _payload(raw: Any, purpose: str) -> Any:
    if isinstance(raw, list):
        return {"items": raw}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise AIOutputValidationError(
                "Invalid AI structured output",
                purpose=purpose,
                attempt_count=1,
                validation_details=["unparseable_ai_json"],
            ) from exc
    return raw


def finite(value: float | None) -> float | None:
    """The value when it is a real number, else None (NaN and inf count as missing)."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def short_name(name: str | None) -> str:
    """Trimmed display name, cut to the stored length."""
    return " ".join((name or "").split())[:DESCRIBE_NAME_MAX_CHARS].strip()
