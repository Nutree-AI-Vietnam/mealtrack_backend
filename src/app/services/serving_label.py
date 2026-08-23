"""Presentation-only serving labels. English unit tokens stay the identity."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from src.app.services.food_display_name import (
    is_ascii_display_name,
    needs_display_localization,
)
from src.domain.services.nutrition_calculation_service import (
    MASS_VOLUME_CANONICAL_UNITS,
)
from src.domain.services.serving_phrase import serving_phrase_key

DISPLAY_DESCRIPTION_KEY = "display_description"

# Keep common units predictable instead of relying on a translation provider
# for vocabulary the product already owns. Provider phrases remain translated
# as complete phrases; mobile decides whether a list needs their compact form.
_VI_CANONICAL_SERVING_LABELS = {
    "serving": "Khẩu phần",
    "piece": "Cái",
    "slice": "Lát",
    "cup": "Cốc",
    "tablespoon": "Muỗng canh",
    "tbsp": "Muỗng canh",
    "teaspoon": "Muỗng cà phê",
    "tsp": "Muỗng cà phê",
    "large": "Lớn",
    "medium": "Vừa",
    "small": "Nhỏ",
}
_VI_SERVING_WORDS = {
    "thin": "mỏng",
    "thick": "dày",
    "large": "lớn",
    "medium": "vừa",
    "small": "nhỏ",
    "slice": "lát",
    "slices": "lát",
    "piece": "cái",
    "pieces": "cái",
    "serving": "khẩu phần",
    "servings": "khẩu phần",
    "cup": "cốc",
    "cups": "cốc",
    "tablespoon": "muỗng canh",
    "tbsp": "muỗng canh",
    "teaspoon": "muỗng cà phê",
    "tsp": "muỗng cà phê",
}
_VI_SERVING_ADJECTIVES = {"mỏng", "dày", "lớn", "vừa", "nhỏ"}


def serving_display_source(option: Mapping[str, Any]) -> str:
    """English phrase to translate: full unit token, not a shortened noun."""
    return str(option.get("unit") or option.get("name") or "").strip()


def canonical_serving_labels(
    options: Iterable[Mapping[str, Any]], language: str
) -> dict[str, str]:
    """Return product-owned localized labels for exact common unit tokens."""
    if language != "vi":
        return {}
    labels: dict[str, str] = {}
    for option in options:
        source = serving_display_source(option)
        key = serving_phrase_key(source)
        if not source:
            continue
        if key in _VI_CANONICAL_SERVING_LABELS:
            labels[key] = _VI_CANONICAL_SERVING_LABELS[key]
            continue
        # Parenthetical provider measurements are detail-only. Comma-qualified
        # phrases such as ``cup, cooked, diced`` carry semantic qualifiers and
        # must remain intact for the phrase translator/cache.
        compact = re.sub(r"\s*\([^)]*\)", "", source).strip()
        words = compact.lower().split()
        translated = [_VI_SERVING_WORDS.get(word, "") for word in words]
        if words and all(translated):
            ordered = [
                value for value in translated if value not in _VI_SERVING_ADJECTIVES
            ] + [value for value in translated if value in _VI_SERVING_ADJECTIVES]
            labels[key] = " ".join(ordered).capitalize()
    return labels


def needs_serving_label(text: str, language: str) -> bool:
    """True when a serving phrase is still English for a non-English user."""
    if not language or language == "en":
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if serving_phrase_key(stripped) in MASS_VOLUME_CANONICAL_UNITS:
        return False
    return needs_display_localization(stripped, language) or is_ascii_display_name(
        stripped
    )


def leftover_serving_phrases(
    options: Iterable[Mapping[str, Any]], language: str
) -> list[str]:
    """Unique English unit phrases that still lack a localized label."""
    leftovers: list[str] = []
    seen: set[str] = set()
    for option in options:
        display = str(option.get(DISPLAY_DESCRIPTION_KEY) or "").strip()
        if display and not needs_serving_label(display, language):
            continue
        source = serving_display_source(option)
        if not needs_serving_label(source, language):
            continue
        key = serving_phrase_key(source)
        if key and key not in seen:
            seen.add(key)
            leftovers.append(source)
    return leftovers


def apply_serving_labels(
    options: list[dict[str, Any]],
    labels_by_key: Mapping[str, str],
    language: str,
) -> list[dict[str, Any]]:
    """Copy localized labels onto options without rewriting unit or description."""
    keyed = {
        serving_phrase_key(source): label
        for source, label in labels_by_key.items()
        if label
    }
    keyed.update(
        {str(source): label for source, label in labels_by_key.items() if label}
    )
    # Canonical labels deliberately override stale phrase-cache translations.
    keyed.update(canonical_serving_labels(options, language))
    updated: list[dict[str, Any]] = []
    for option in options:
        copied = dict(option)
        source = serving_display_source(copied)
        label = (
            keyed.get(serving_phrase_key(source), "") or keyed.get(source, "")
        ).strip()
        if label and not needs_serving_label(label, language):
            copied[DISPLAY_DESCRIPTION_KEY] = label[:100]
        updated.append(copied)
    return updated


def merge_serving_labels(
    labels: Mapping[str, str],
    options: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Copy ``display_description`` values onto phrase keys."""
    merged = dict(labels)
    for option in options:
        source = serving_display_source(option)
        display = str(option.get(DISPLAY_DESCRIPTION_KEY) or "").strip()
        if source and display:
            merged[serving_phrase_key(source)] = display[:100]
    return merged


def overlay_serving_labels(
    options: list[Any],
    labels_by_unit: Mapping[str, str],
    *,
    language: str = "",
) -> list[dict[str, Any]]:
    """Apply catalog ``name_vi`` labels keyed by English unit."""
    canonical = canonical_serving_labels(
        [option for option in options if isinstance(option, Mapping)], language
    )
    updated: list[dict[str, Any]] = []
    for option in options:
        copied = dict(option) if isinstance(option, Mapping) else {}
        if not copied:
            continue
        unit_key = serving_phrase_key(serving_display_source(copied))
        label = (
            canonical.get(unit_key) or str(labels_by_unit.get(unit_key) or "").strip()
        )
        if label and (
            unit_key in canonical
            or not str(copied.get(DISPLAY_DESCRIPTION_KEY) or "").strip()
        ):
            copied[DISPLAY_DESCRIPTION_KEY] = label[:100]
        updated.append(copied)
    return updated
