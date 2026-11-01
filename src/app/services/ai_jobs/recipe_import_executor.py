"""Run one attempt of a background recipe import."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from src.app.commands.recipe_import import ImportRecipeCommand
from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim

Send = Callable[[Any], Awaitable[Any]]


class RecipeImportJobExecutor:
    """Reads the source into a draft; the draft itself is the job result.

    Nothing is saved, so a retry after a lost lease only repeats the read.
    """

    def __init__(self, *, send: Send, to_result: Callable[[Any], dict[str, Any]]):
        self._send = send
        self._to_result = to_result

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        payload = claim.request_payload
        recipe = await self._send(
            ImportRecipeCommand(
                user_id=claim.user_id,
                language=claim.language,
                url=_optional_str(payload, "url"),
                text=_optional_str(payload, "text"),
                image_url=_optional_str(payload, "image_url"),
                note=_optional_str(payload, "note"),
            )
        )
        return {"recipe": self._to_result(recipe)}


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return value or None
