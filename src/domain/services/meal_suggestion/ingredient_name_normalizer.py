"""
Ingredient name normalizer — single source of truth for food name normalization.

Used when populating food_reference.name_normalized and when matching
AI-generated ingredient names against the food reference table.
"""

import re
import unicodedata

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

# Cooking and preservation state change the nutrition identity. Keep the older
# normalizer below for existing search/import contracts; nutrition lookups use
# this state-preserving key and a versioned cache namespace.
_IDENTITY_REMOVABLE_QUALIFIERS = [
    "boneless",
    "skinless",
    "organic",
    "large",
    "medium",
    "small",
    "sliced",
    "diced",
    "chopped",
    "minced",
    "fresh",
    "frozen",
]
_IDENTITY_QUAL_RE = re.compile(
    r"\b(" + "|".join(map(re.escape, _IDENTITY_REMOVABLE_QUALIFIERS)) + r")\b",
    re.IGNORECASE,
)

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


def normalize_food_identity(name: str) -> str:
    """Normalize cosmetic wording while preserving nutrition-relevant state."""
    name = unicodedata.normalize("NFKD", name.casefold().replace("đ", "d"))
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = _IDENTITY_QUAL_RE.sub(" ", name)
    name = re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE)
    return " ".join(name.split())
