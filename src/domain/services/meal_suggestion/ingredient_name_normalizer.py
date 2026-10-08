"""
Ingredient name normalizer — single source of truth for food name normalization.

Used when populating food_reference.name_normalized and when matching
AI-generated ingredient names against the food reference table.
"""

import re

_QUALIFIERS = [
    "raw",
    "cooked",
    "boiled",
    "fried",
    "grilled",
    "baked",
    "roasted",
    "boneless",
    "skinless",
    "fresh",
    "frozen",
    "canned",
    "dried",
    "organic",
    "large",
    "medium",
    "small",
    "whole",
    "sliced",
    "diced",
    "chopped",
    "minced",
]

# Preparation and size descriptors that never identify a food on their own.
FOOD_NAME_QUALIFIERS = frozenset(_QUALIFIERS)

# Compiled once at module load: word-boundary anchored, case-insensitive.
# Using \b ensures "raw" matches standalone "raw" but NOT "Strawberry" or "Freshwater".
_QUAL_RE = re.compile(
    r"\b(" + "|".join(map(re.escape, _QUALIFIERS)) + r")\b",
    re.IGNORECASE,
)


def normalize_food_name(name: str) -> str:
    """Normalize ingredient name for consistent matching.

    Steps:
    1. Lowercase and strip surrounding whitespace.
    2. Remove cooking-method and descriptor qualifiers using word-boundary
       regex to avoid partial matches (e.g. "raw" does NOT touch "Strawberry").
    3. Strip remaining punctuation (commas, parentheses, etc.).
    4. Collapse internal whitespace.

    Args:
        name: Raw ingredient name, e.g. "Chicken Breast, Boneless".

    Returns:
        Normalized form, e.g. "chicken breast".
    """
    name = name.lower().strip()
    name = _QUAL_RE.sub(" ", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)  # strip punctuation
    return " ".join(name.split())
