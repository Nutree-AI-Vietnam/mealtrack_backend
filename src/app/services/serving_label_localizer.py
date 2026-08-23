"""Localize FatSecret serving phrases: database first, translator last."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.app.services.food_name_localizer import (
    translate_food_texts,
    translated_values,
    translation_is_cacheable,
)
from src.app.services.serving_label import (
    apply_serving_labels,
    canonical_serving_labels,
    leftover_serving_phrases,
    needs_serving_label,
    serving_phrase_key,
)
from src.domain.constants.languages import normalize_language

__all__ = ["localize_item_servings", "localize_serving_options"]


def _empty_units(options: Any) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    return [dict(option) for option in options if isinstance(option, Mapping)]


async def localize_serving_options(
    options: list[Any],
    *,
    language: str,
    translation_service: Any | None,
    cached_labels: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return options with ``display_description`` filled when possible."""
    copied = _empty_units(options)
    normalized = normalize_language(language)
    if not copied or normalized == "en":
        return copied
    labeled = apply_serving_labels(copied, cached_labels or {}, normalized)
    leftovers = leftover_serving_phrases(labeled, normalized)
    if not leftovers or translation_service is None:
        return labeled
    result = await translate_food_texts(
        leftovers,
        target_language=normalized,
        translation_service=translation_service,
    )
    fresh = {
        serving_phrase_key(source): str(translated).strip()
        for source, translated in zip(
            leftovers, translated_values(leftovers, result), strict=False
        )
        if str(translated).strip()
        and not needs_serving_label(str(translated), normalized)
    }
    if not fresh:
        return labeled
    return apply_serving_labels(labeled, fresh, normalized)


async def localize_item_servings(
    items: list[dict[str, Any]],
    *,
    language: str,
    translation_service: Any | None,
    uow_factory: Any | None = None,
    persist: bool = False,
) -> dict[str, str]:
    """Localize each item's ``allowed_units`` and optionally persist labels.

    Returns newly cacheable phrase translations (English source → label).
    """
    normalized = normalize_language(language)
    if not items or normalized == "en":
        return {}
    cached = await _load_phrase_cache(items, normalized, uow_factory)
    canonical: dict[str, str] = {}
    leftovers: list[str] = []
    seen: set[str] = set()
    for item in items:
        options = _empty_units(item.get("allowed_units"))
        canonical.update(canonical_serving_labels(options, normalized))
        item["allowed_units"] = apply_serving_labels(options, cached, normalized)
        for phrase in leftover_serving_phrases(item["allowed_units"], normalized):
            key = serving_phrase_key(phrase)
            if key not in seen:
                seen.add(key)
                leftovers.append(phrase)
    fresh, cacheable = await _translate_leftovers(
        leftovers, normalized, translation_service
    )
    resolved = {**canonical, **fresh}
    if not resolved:
        return {}
    for item in items:
        item["allowed_units"] = apply_serving_labels(
            _empty_units(item.get("allowed_units")),
            resolved,
            normalized,
        )
    if persist and uow_factory is not None and (canonical or cacheable):
        await _persist_labels(items, resolved, normalized, uow_factory)
    return resolved


async def _translate_leftovers(
    leftovers: list[str],
    language: str,
    translation_service: Any | None,
) -> tuple[dict[str, str], bool]:
    if not leftovers or translation_service is None:
        return {}, False
    result = await translate_food_texts(
        leftovers,
        target_language=language,
        translation_service=translation_service,
    )
    labels = {
        source: str(translated).strip()
        for source, translated in zip(
            leftovers, translated_values(leftovers, result), strict=False
        )
        if str(translated).strip()
        and not needs_serving_label(str(translated), language)
    }
    return labels, bool(labels) and translation_is_cacheable(result)


async def _load_phrase_cache(
    items: list[dict[str, Any]],
    language: str,
    uow_factory: Any | None,
) -> dict[str, str]:
    phrases: list[str] = []
    for item in items:
        phrases.extend(
            leftover_serving_phrases(_empty_units(item.get("allowed_units")), language)
        )
    if not phrases or uow_factory is None:
        return {}
    try:
        async with uow_factory() as uow:
            return await uow.food_references.get_serving_phrase_translations(
                phrases, language
            )
    except Exception:
        return {}


async def _persist_labels(
    items: list[dict[str, Any]],
    labels_by_source: dict[str, str],
    language: str,
    uow_factory: Any,
) -> None:
    by_reference: dict[int, dict[str, str]] = {}
    for item in items:
        reference_id = item.get("food_reference_id")
        if reference_id is None:
            continue
        keyed = {
            serving_phrase_key(source): label
            for source, label in labels_by_source.items()
        }
        for option in _empty_units(item.get("allowed_units")):
            source = str(option.get("unit") or "").strip()
            label = labels_by_source.get(source) or keyed.get(
                serving_phrase_key(source)
            )
            if label:
                by_reference.setdefault(int(reference_id), {})[source] = label
    try:
        async with uow_factory() as uow:
            await uow.food_references.upsert_serving_phrase_translations(
                labels_by_source, language
            )
            for reference_id, labels in by_reference.items():
                await uow.food_references.apply_serving_name_vi(reference_id, labels)
    except Exception:
        return
