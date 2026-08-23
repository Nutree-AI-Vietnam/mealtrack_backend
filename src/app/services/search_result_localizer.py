"""Keep catalog search display names aligned with the request language."""

from __future__ import annotations

from typing import Any

from src.app.services.food_display_name import (
    apply_glossary_display_names,
    apply_localized_display_names,
    leftover_display_names,
)
from src.app.services.food_name_localizer import translate_food_texts
from src.domain.model.translation_result import TranslationOutcome


def _seed_search_display_names(results: list[dict[str, Any]]) -> None:
    for item in results:
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name and description:
            item["name"] = description
        elif name and not description:
            item["description"] = name


def _publish_search_display_names(results: list[dict[str, Any]]) -> None:
    for item in results:
        display = str(item.get("name") or item.get("description") or "").strip()
        if not display:
            continue
        item["name"] = display
        item["description"] = display


def _apply_name_vi(results: list[dict[str, Any]], language: str) -> bool:
    if language != "vi":
        return False
    applied_all = True
    for item in results:
        name_vi = str(item.get("name_vi") or "").strip()
        if not name_vi:
            applied_all = False
            continue
        item["name"] = name_vi
        item["description"] = name_vi
    return bool(results) and applied_all


async def localize_search_result_names(
    results: list[dict[str, Any]],
    *,
    language: str,
    translation_service: Any | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Localize search rows. Cacheable only when no English leftovers remain."""
    if not results or language == "en":
        return results, True
    localized = [item.copy() for item in results]
    if _apply_name_vi(localized, language):
        return localized, True

    _seed_search_display_names(localized)
    for _ in range(2):
        leftovers = leftover_display_names(localized, language)
        if not leftovers:
            _publish_search_display_names(localized)
            return localized, True
        result = await translate_food_texts(
            leftovers,
            target_language=language,
            translation_service=translation_service,
        )
        apply_localized_display_names(
            localized,
            dict(zip(leftovers, result.texts, strict=False)),
            language,
        )
        apply_glossary_display_names(localized, language)
        if result.outcome is not TranslationOutcome.TRANSLATED:
            break

    apply_glossary_display_names(localized, language)
    _publish_search_display_names(localized)
    return localized, not leftover_display_names(localized, language)
