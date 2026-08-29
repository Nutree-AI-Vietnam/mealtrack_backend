"""Decide when parse-text should expand a dish versus keep listed foods."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

ParseTextInputKind = Literal["dish", "ingredient_list", "single_food"]

_LIST_SPLIT = re.compile(
    r"\s*(?:,|;|/|\n|\+| and | và | with | với | plus )\s*",
    re.IGNORECASE,
)
_MASS_VOLUME = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:g|gram|grams|kg|ml|l|oz|lb)s?\b",
    re.IGNORECASE,
)
_QUANTITY_UNIT = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*[A-Za-zÀ-ỹ%]+\b",
    re.IGNORECASE,
)
_REFINEMENT_PREFIX = re.compile(
    r"^(?:thêm|them|bớt|bot|add|remove|extra|plus|minus|with no|no)\s+",
    re.IGNORECASE,
)
_DISH_MARKERS = frozenset(
    {
        "biryani",
        "bowl",
        "bun",
        "bún",
        "burger",
        "burrito",
        "casserole",
        "com",
        "cơm",
        "curry",
        "dish",
        "gỏi cuốn",
        "goi cuon",
        "hotpot",
        "hủ tiếu",
        "hu tieu",
        "lasagna",
        "lẩu",
        "lau",
        "meal",
        "mi",
        "mì",
        "noodle",
        "paella",
        "pad thai",
        "padthai",
        "pasta",
        "pho",
        "phở",
        "pizza",
        "platter",
        "ramen",
        "risotto",
        "salad",
        "sandwich",
        "soup",
        "steak",
        "stew",
        "taco",
        "wellington",
        "wrap",
    }
)
_DISH_UNITS = frozenset({"bát", "bowl", "ổ", "plate", "tô", "đĩa"})
_SINGLE_FOOD_MARKERS = frozenset(
    {
        "apple",
        "banana",
        "butter",
        "cheese",
        "chuối",
        "chuoi",
        "dầu",
        "dau",
        "egg",
        "honey",
        "juice",
        "milk",
        "oil",
        "sữa",
        "sua",
        "sữa chua",
        "sua chua",
        "táo",
        "tao",
        "trứng",
        "trung",
        "yogurt",
    }
)


def classify_parse_text_input(text: str) -> ParseTextInputKind:
    """Classify a parse-text utterance as a dish, listed foods, or one measured food."""
    compact = " ".join(text.strip().split())
    if not compact:
        return "single_food"

    # Strip refinement prefix for classification
    clean_text = _REFINEMENT_PREFIX.sub("", compact).strip()
    if not clean_text:
        clean_text = compact

    parts = [part for part in _LIST_SPLIT.split(clean_text) if part.strip()]
    if len(parts) >= 2:
        return "ingredient_list"
    if len(_QUANTITY_UNIT.findall(clean_text)) >= 2:
        return "ingredient_list"
    if _MASS_VOLUME.search(clean_text):
        return "single_food"
    if _looks_like_named_dish(clean_text):
        return "dish"
    return "single_food"


def composition_retry_feedback(text: str, items: list[dict[str, Any]]) -> str | None:
    """Ask the model to expand a named dish returned as a single row."""
    if classify_parse_text_input(text) != "dish":
        return None
    if len(items) != 1:
        return None
    return (
        "The user named a prepared dish. Return the edible components of one "
        "serving, not the dish as a single row. Do not recurse into recipes."
    )


def _looks_like_named_dish(text: str) -> bool:
    normalized = _normalize_for_search(text)
    # Check if text contains single food markers (like yogurt, apple, egg) without composite dish markers
    tokens = _tokens(text)
    if tokens & _SINGLE_FOOD_MARKERS:
        # If it contains single food (like yogurt/egg) without explicit dish units (tô/đĩa/bowl/plate), it's single food
        if not (tokens & _DISH_UNITS) and not any(
            marker in normalized
            for marker in ("wellington", "casserole", "lasagna", "curry", "stew")
        ):
            return False

    for marker in _DISH_MARKERS:
        if re.search(rf"\b{re.escape(marker)}\b", normalized):
            return True
    if tokens & _DISH_UNITS:
        return True
    return False


def _normalize_for_search(text: str) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _tokens(text: str) -> set[str]:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return set(re.findall(r"[a-z0-9]+", folded))
