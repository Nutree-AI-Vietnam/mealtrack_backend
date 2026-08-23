"""Detect leftover English food names and apply presentation-only replacements."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_SEGMENT_SPLIT = re.compile(r"([/|,;&]+)")
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*")
_ENGLISH_CONNECTORS = re.compile(
    r"\b(and|with|of|the|in|from|style)\b", re.IGNORECASE
)
_NON_GLOSSARY = re.compile(r"[^a-z]+")

_CLEAR_ENGLISH_FOOD_TOKENS = frozenset(
    "baguette beef bread broth butter chicken cilantro coriander cucumber egg "
    "eggs fish fried grilled knuckle lettuce mayo mayonnaise milk noodle noodles "
    "oil pork potato rice salad sauce shredded shrimp skin soup steamed "
    "vermicelli vietnamese".split()
)
_ROMANIZED_VIETNAMESE_TOKENS = frozenset(
    "banh bun cha che chien chua com dua gao gio goi hap hanh heo hue khoai "
    "lang luoc mam nem ngam ngo nuoc nuong pho quang rau tay thit tieu toi tom "
    "tuoi xao xoi".split()
)
_GENERIC_FALLBACK = {
    "vi": "Nguyên liệu",
    "es": "Ingrediente",
    "fr": "Ingrédient",
    "de": "Zutat",
    "ja": "食材",
    "zh": "食材",
}
_GLOSSARY = {
    "vi": {
        "apple": "Táo",
        "baguette": "Bánh mì",
        "banana": "Chuối",
        "bananas": "Chuối",
        "beef": "Thịt bò",
        "beef broth": "Nước dùng bò",
        "broth": "Nước dùng",
        "chicken": "Gà",
        "chicken broth": "Nước dùng gà",
        "cilantro": "Rau mùi",
        "coriander": "Rau mùi",
        "egg": "Trứng",
        "eggs": "Trứng",
        "fried rice": "Cơm chiên",
        "mayo": "Sốt mayonnaise",
        "mayonnaise": "Sốt mayonnaise",
        "milk": "Sữa",
        "rose apple": "Táo hồng",
        "whole milk": "Sữa tươi",
        "pork pate": "Pate heo",
        "rice": "Cơm",
        "tomato sauce": "Sốt cà chua",
        "vegetable broth": "Nước dùng rau",
        "vietnamese baguette": "Bánh mì",
    }
}


def fold_latin_display_name(name: str) -> str:
    """Strip combining marks so accented English (Pâté) compares as Latin."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def is_ascii_display_name(name: str) -> bool:
    """True when a display name has no localized letters."""
    stripped = name.strip()
    return bool(stripped) and all(ord(character) < 128 for character in stripped)


def needs_display_localization(name: str, language: str) -> bool:
    """True when leftover display text still contains English for a non-English user."""
    if not language or language == "en":
        return False
    stripped = name.strip()
    return bool(stripped) and any(
        _segment_is_english(segment) for segment in _iter_segments(stripped)
    )


def leftover_display_names(items: list[dict[str, Any]], language: str) -> list[str]:
    """Unique leftover names or slash-separated English segments to translate."""
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if not needs_display_localization(name, language):
            continue
        segments = _iter_segments(name)
        targets = (
            [segment for segment in segments if _segment_is_english(segment)]
            if len(segments) > 1
            else [name]
        )
        for target in targets:
            if target not in seen:
                seen.add(target)
                names.append(target)
    return names


def apply_localized_display_names(
    items: list[dict[str, Any]],
    translated_by_source: dict[str, Any],
    language: str,
) -> None:
    """Replace leftover names/segments when the translated value is localized."""
    for item in items:
        name = str(item.get("name") or "").strip()
        updated = _replace_localized_name(name, translated_by_source, language)
        if updated != name:
            item["name"] = updated


def apply_glossary_display_names(
    items: list[dict[str, Any]], language: str
) -> None:
    """Apply deterministic display names for known leftover English foods."""
    mapping = {
        leftover: localized
        for leftover in leftover_display_names(items, language)
        if (localized := glossary_localized_name(leftover, language))
    }
    if mapping:
        apply_localized_display_names(items, mapping, language)


def apply_fail_closed_display_names(
    items: list[dict[str, Any]], language: str
) -> None:
    """Replace remaining English leftovers with a glossary or generic fallback."""
    leftovers = leftover_display_names(items, language)
    if not leftovers:
        return
    generic = fallback_localized_name(language)
    mapping = {
        leftover: glossary_localized_name(leftover, language) or generic
        for leftover in leftovers
    }
    apply_localized_display_names(items, mapping, language)


def glossary_localized_name(name: str, language: str) -> str | None:
    folded = _NON_GLOSSARY.sub(" ", fold_latin_display_name(name).lower()).strip()
    return _GLOSSARY.get(language, {}).get(folded)


def fallback_localized_name(language: str) -> str:
    return _GENERIC_FALLBACK.get(language, "Nguyên liệu")


def _iter_segments(name: str) -> list[str]:
    return [
        part.strip()
        for part in _SEGMENT_SPLIT.split(name)
        if part.strip() and not _SEGMENT_SPLIT.fullmatch(part)
    ]


def _segment_is_english(segment: str) -> bool:
    if not segment or any(ord(char) > 255 for char in segment if char.isalpha()):
        return False
    folded = fold_latin_display_name(segment).strip()
    tokens = _LATIN_TOKEN.findall(folded.lower())
    if not tokens:
        return False
    if _ENGLISH_CONNECTORS.search(folded):
        return True
    if any(token in _CLEAR_ENGLISH_FOOD_TOKENS for token in tokens):
        return True
    if any(token in _ROMANIZED_VIETNAMESE_TOKENS for token in tokens):
        return False
    return any(len(token) >= 4 for token in tokens)


def _replace_localized_name(
    name: str, translated_by_source: dict[str, Any], language: str
) -> str:
    exact = translated_by_source.get(name)
    if _usable_localized_name(exact, language):
        return str(exact).strip()
    pieces = _SEGMENT_SPLIT.split(name)
    if len(pieces) == 1:
        return name
    replaced: list[str] = []
    changed = False
    for piece in pieces:
        if _SEGMENT_SPLIT.fullmatch(piece):
            replaced.append(piece)
            continue
        stripped = piece.strip()
        translated = translated_by_source.get(stripped)
        if _usable_localized_name(translated, language):
            replaced.append(piece.replace(stripped, str(translated).strip(), 1))
            changed = True
        else:
            replaced.append(piece)
    return "".join(replaced) if changed else name


def _usable_localized_name(localized: Any, language: str) -> bool:
    if not isinstance(localized, str) or not localized.strip():
        return False
    return not needs_display_localization(localized.strip(), language)
