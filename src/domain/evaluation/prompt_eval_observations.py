"""Normalize recorded candidate payloads into observation records."""

from __future__ import annotations

from typing import Any

from .prompt_eval_models import PromptEvalObservation


def observations_for(
    value: Any, fallback: dict[str, Any]
) -> list[PromptEvalObservation]:
    if value is None:
        return [PromptEvalObservation(response_payload=fallback)]
    if isinstance(value, dict) and "response_payload" in value:
        return [PromptEvalObservation(**value)]
    if isinstance(value, list):
        return [
            item
            if isinstance(item, PromptEvalObservation)
            else PromptEvalObservation(**item)
            for item in value
        ]
    if isinstance(value, dict):
        return [PromptEvalObservation(response_payload=value)]
    raise ValueError("candidate observations must be payloads or observation objects")
