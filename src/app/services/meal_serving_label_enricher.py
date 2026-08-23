"""Fill leftover FatSecret serving labels on meal-detail projections."""

from __future__ import annotations

from typing import Any

from src.app.services.serving_label import (
    canonical_serving_labels,
    leftover_serving_phrases,
    merge_serving_labels,
    overlay_serving_labels,
)
from src.app.services.serving_label_localizer import localize_item_servings
from src.domain.constants.languages import normalize_language

__all__ = ["enrich_meal_serving_labels"]


async def enrich_meal_serving_labels(
    meal: Any,
    projections: dict[int, dict[str, Any]],
    *,
    language: str,
    translation_service: Any | None,
    uow_factory: Any | None,
) -> dict[int, dict[str, Any]]:
    """Translate leftover serving phrases and merge them into projections."""
    normalized = normalize_language(language)
    if not projections or normalized == "en":
        return projections
    items = _meal_serving_payloads(meal, projections, normalized)
    if not items:
        return projections
    await localize_item_servings(
        items,
        language=normalized,
        translation_service=translation_service,
        uow_factory=uow_factory,
        persist=True,
    )
    for payload in items:
        reference_id = payload.get("food_reference_id")
        if reference_id is None:
            continue
        bucket = projections.setdefault(int(reference_id), {"serving_labels": {}})
        bucket["serving_labels"] = merge_serving_labels(
            bucket.get("serving_labels") or {},
            payload.get("serving_options") or [],
        )
    return projections


def _meal_serving_payloads(
    meal: Any, projections: dict[int, dict[str, Any]], language: str
) -> list[dict[str, Any]]:
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    payloads: list[dict[str, Any]] = []
    for item in food_items:
        reference_id = getattr(item, "food_reference_id", None)
        labels = (projections.get(reference_id) or {}).get("serving_labels") or {}
        options = overlay_serving_labels(
            getattr(item, "serving_options", None) or [],
            labels,
            language=language,
        )
        if canonical_serving_labels(options, language) or leftover_serving_phrases(
            options, language
        ):
            payloads.append(
                {
                    "food_reference_id": reference_id,
                    "serving_options": options,
                }
            )
    return payloads
