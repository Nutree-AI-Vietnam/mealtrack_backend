"""A second, narrow read for Ghi nhanh items the model named but left blank.

An activity or drink outside Nutree's catalogs needs the model's own numbers:
a MET, or values per 100 ml. When the first read of the sentence leaves them
out, one short call asks for just those items, by name. The call is best
effort: when it fails or misses an item, the normalisers fall back to safe
defaults. Either way the item is saved, so quick add never drops something
the person logged because the catalog lacks it.
"""

import logging
from typing import Any

from src.app.services.describe_hydration_items import needs_estimate
from src.app.services.describe_log_parser import (
    DESCRIBE_MAX_ITEMS,
    finite,
    parse_describe_sentence,
    short_name,
)
from src.app.services.describe_movement_items import needs_met
from src.domain.model.ai.describe_log_contracts import UnlistedEstimateParse
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.prompts.system_prompts import SystemPrompts

logger = logging.getLogger(__name__)

# Short on purpose: the person is waiting, and the defaults are good enough.
UNLISTED_ESTIMATE_TIMEOUT_SECONDS = 6.0
UNLISTED_ESTIMATE_MAX_TOKENS = 800
_ACTIVITY, _DRINK = "activity", "drink"
# Characters that could break the one-line-per-item list or its delimiters.
_LINE_UNSAFE = str.maketrans({"<": " ", ">": " ", "|": " ", "\n": " ", "\r": " "})


async def fill_unlisted_estimates(
    generation_service: MealGenerationServicePort,
    *,
    activities: list[dict[str, Any]],
    drinks: list[dict[str, Any]],
    timeout_seconds: float = UNLISTED_ESTIMATE_TIMEOUT_SECONDS,
    model_purpose: str = "parse_text",
) -> int:
    """Fill the blank numbers of these model items in place.

    Returns how many items got numbers from this call; the rest keep their
    blanks for the normalisers' defaults. Never raises for a failed read.
    """
    lines = [
        (f"a{index}", _ACTIVITY, item)
        for index, item in enumerate(activities[:DESCRIBE_MAX_ITEMS])
        if needs_met(item)
    ] + [
        (f"d{index}", _DRINK, item)
        for index, item in enumerate(drinks[:DESCRIBE_MAX_ITEMS])
        if needs_estimate(item)
    ]
    if not lines:
        return 0

    try:
        reply = await parse_describe_sentence(
            generation_service,
            sentence="\n".join(_line(ref, kind, item) for ref, kind, item in lines),
            system_prompt=SystemPrompts.get_unlisted_estimate_prompt(),
            schema=UnlistedEstimateParse,
            purpose="describe_unlisted_estimate",
            timeout_seconds=timeout_seconds,
            model_purpose=model_purpose,
            max_tokens=UNLISTED_ESTIMATE_MAX_TOKENS,
        )
    except Exception as exc:  # best effort: the defaults cover a failed read
        logger.warning(
            "[DESCRIBE-LOG-ESTIMATE-FAILED] items=%d error=%s",
            len(lines),
            type(exc).__name__,
        )
        return 0

    # Each line takes the first answer of its own kind; any other ref is ignored.
    activity_lines = {ref: item for ref, kind, item in lines if kind == _ACTIVITY}
    drink_lines = {ref: item for ref, kind, item in lines if kind == _DRINK}
    filled = 0
    for estimate in reply.get("activities") or []:
        item = activity_lines.pop(str(estimate.get("ref")), None)
        if item is not None and finite(estimate.get("met")) is not None:
            item["met"] = estimate["met"]
            filled += 1
    for estimate in reply.get("drinks") or []:
        item = drink_lines.pop(str(estimate.get("ref")), None)
        values = estimate.get("unlisted") or {}
        if item is not None and finite(values.get("hydration_weight")) is not None:
            item["unlisted"] = values
            filled += 1
    return filled


def _line(ref: str, kind: str, item: dict[str, Any]) -> str:
    name = short_name(str(item.get("name") or "").translate(_LINE_UNSAFE))
    effort = str(item.get("intensity") or "").translate(_LINE_UNSAFE).strip()
    return f"{ref} | {kind} | {name} | {effort if kind == _ACTIVITY else ''}".rstrip()
