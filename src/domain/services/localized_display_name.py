"""Locale presentation helpers for meal and ingredient display names.

A meal keeps more than one dish name: the stored ``dish_name``, the canonical
and same-call localized names inside the AI payload, and one translation row
per locale. Reads prefer those locale names over the stored one, so a name the
user saves must reach all of them or the old name comes back on the next GET.
"""

import json
from dataclasses import replace
from typing import Any

from src.domain.constants.languages import normalize_language
from src.domain.model.meal.meal import Meal
from src.domain.model.meal.meal_response_localization import (
    parse_meal_response_localization,
)
from src.domain.model.meal.meal_translation_domain_models import (
    CURRENT_MEAL_TRANSLATION_VERSION,
)


def is_ascii_latin_label(name: str | None) -> bool:
    """Return whether a label is unaccented Latin catalog text."""
    stripped = (name or "").strip()
    if not stripped:
        return False
    return stripped.isascii() and any(character.isalpha() for character in stripped)


def already_in_target_language(name: str | None, language: str | None) -> bool:
    """Return whether a stored label is already localized for the locale."""
    target = normalize_language(language)
    if target == "en":
        return False
    stripped = (name or "").strip()
    return bool(stripped) and not is_ascii_latin_label(stripped)


def keep_stored_display_name(
    *,
    stored: str | None,
    translated: str | None,
    language: str | None,
) -> bool:
    """Keep a localized stored name instead of leftover English catalog text."""
    target = normalize_language(language)
    if target == "en":
        return False
    stored_name = (stored or "").strip()
    translated_name = (translated or "").strip()
    if not stored_name or not translated_name or stored_name == translated_name:
        return False
    return is_ascii_latin_label(translated_name) and not is_ascii_latin_label(
        stored_name
    )


_PERSISTED_IMAGE_NAME_SOURCES = frozenset({"scanner", "food_label"})
_RAW_DISH_NAME_KEYS = ("dish_name", "localized_dish_name")


def has_persisted_image_display_names(meal: Meal) -> bool:
    """Return whether an image meal can trust its stored display names."""
    return getattr(meal, "source", None) in _PERSISTED_IMAGE_NAME_SOURCES and not (
        getattr(meal, "translations", None)
    )


def parse_raw_payload(meal: Meal) -> Any:
    """Decode the stored AI payload, or None when it is missing or invalid."""
    if not meal.raw_gpt_json:
        return None
    try:
        return json.loads(meal.raw_gpt_json)
    except (TypeError, ValueError):
        return None


def display_dish_name(
    meal: Meal,
    language: str | None,
    *,
    parsed_raw: Any = None,
) -> str | None:
    """Return the dish name a meal read in ``language`` shows."""
    dish_name = meal.dish_name
    if not language or has_persisted_image_display_names(meal):
        return dish_name
    requested = normalize_language(language)
    raw = parsed_raw if parsed_raw is not None else parse_raw_payload(meal)

    if requested == "en":
        canonical = raw.get("dish_name") if isinstance(raw, dict) else None
        if isinstance(canonical, str) and canonical.strip():
            return canonical.strip()
        return dish_name

    food_items = (meal.nutrition.food_items or []) if meal.nutrition else []
    try:
        localization = parse_meal_response_localization(
            raw,
            requested,
            expected_food_count=len(food_items),
        )
    except (TypeError, ValueError):
        localization = None
    if localization is not None:
        return localization.dish_name

    translation = (meal.translations or {}).get(requested)
    if (
        translation
        and translation.translation_version == CURRENT_MEAL_TRANSLATION_VERSION
        and translation.dish_name
        and not keep_stored_display_name(
            stored=dish_name,
            translated=translation.dish_name,
            language=requested,
        )
    ):
        return translation.dish_name
    return dish_name


def carry_dish_name_to_locales(meal: Meal) -> Meal:
    """Write the stored dish name over every locale name a read prefers."""
    name = (meal.dish_name or "").strip()
    if not name:
        return meal
    translations = (
        {
            language: replace(translation, dish_name=name)
            for language, translation in meal.translations.items()
        }
        if meal.translations
        else meal.translations
    )
    return replace(
        meal,
        translations=translations,
        raw_gpt_json=_renamed_raw_payload(meal, name),
    )


def _renamed_raw_payload(meal: Meal, name: str) -> str | None:
    raw = parse_raw_payload(meal)
    if not isinstance(raw, dict):
        return meal.raw_gpt_json
    keys = [key for key in _RAW_DISH_NAME_KEYS if isinstance(raw.get(key), str)]
    if not keys:
        return meal.raw_gpt_json
    for key in keys:
        raw[key] = name
    return json.dumps(raw, ensure_ascii=False)
