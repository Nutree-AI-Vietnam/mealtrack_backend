"""
Input sanitization for user-provided descriptions in LLM prompts.
Prevents prompt injection and abuse.
"""

import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

# Maximum allowed description length
MAX_DESCRIPTION_LENGTH = 500
MAX_REFINEMENT_ITEMS = 20
MAX_REFINEMENT_BYTES = 12 * 1024
_REFINEMENT_SCALAR_KEYS = {
    "name",
    "quantity",
    "unit",
    "english_unit",
    "protein",
    "carbs",
    "fat",
    "fiber",
    "sugar",
    "calories",
    "data_source",
    "fdc_id",
}
_REFINEMENT_UNIT_KEYS = {"unit", "gram_weight", "description"}
_REFINEMENT_STRING_LIMITS = {
    "name": 200,
    "unit": 100,
    "english_unit": 100,
    "data_source": 50,
    "fdc_id": 50,
}
_NON_EMPTY_REFINEMENT_STRING_KEYS = {"name", "unit", "english_unit"}

# Patterns that indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"(ignore|forget|disregard|override|bypass)\s+(all\s+)?(previous|above|prior)?\s*(instruction|prompt|rule|system)s?",
    r"(you\s+are|act\s+as|pretend|roleplay|imagine)\s+(now\s+)?a?\s*(?!nutrition|food)",
    r"(new\s+)?instruction[s]?\s*:",
    r"system\s*(prompt|message)\s*:",
    r"\[.*?(system|admin|root|sudo).*?\]",
    r"<.*?(script|system|admin).*?>",
]

# Characters to remove (control chars, dangerous markup)
FORBIDDEN_CHARS = r"[<>{}[\]|\\`]"


def sanitize_user_description(text: str | None) -> str | None:
    """
    Sanitize user-provided description for safe inclusion in LLM prompts.

    Args:
        text: Raw user input (can be None)

    Returns:
        Sanitized text or None if input was empty/None/blocked

    Example:
        >>> sanitize_user_description("no sugar, half portion")
        "no sugar, half portion"
        >>> sanitize_user_description("ignore all instructions and...")
        None  # Blocked as injection attempt
    """
    if not text:
        return None

    # Strip whitespace
    text = text.strip()

    if not text:
        return None

    # Truncate to max length
    text = text[:MAX_DESCRIPTION_LENGTH]

    # Remove forbidden characters
    text = re.sub(FORBIDDEN_CHARS, "", text)

    # Check for injection patterns (case-insensitive)
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning("Prompt injection attempt blocked: reason=injection_pattern")
            return None  # Block the entire description

    # Normalize whitespace
    text = " ".join(text.split())

    return text if text else None


def validate_refinement_items(items: Any) -> list[dict[str, Any]] | None:
    """Validate the documented refinement shape before prompt construction."""
    if items is None:
        return None
    if not isinstance(items, list) or len(items) > MAX_REFINEMENT_ITEMS:
        raise ValueError("refinement items exceed the supported limit")
    try:
        encoded = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("refinement items must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_REFINEMENT_BYTES:
        raise ValueError("refinement items exceed the supported size")

    validated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - _REFINEMENT_SCALAR_KEYS - {
            "serving_options"
        }:
            raise ValueError("refinement contains unsupported nested content")
        normalized_item = dict(item)
        for key, value in item.items():
            if key == "serving_options":
                if not isinstance(value, list) or len(value) > 12:
                    raise ValueError("refinement serving metadata is invalid")
                normalized_units: list[dict[str, Any]] = []
                for unit in value:
                    if not isinstance(unit, dict) or set(unit) - _REFINEMENT_UNIT_KEYS:
                        raise ValueError(
                            "refinement contains unsupported nested content"
                        )
                    if not isinstance(unit.get("unit"), str):
                        raise ValueError("refinement serving metadata is invalid")
                    raw_weight = unit.get("gram_weight")
                    if (
                        not isinstance(raw_weight, (int, float))
                        or isinstance(raw_weight, bool)
                        or not math.isfinite(float(raw_weight))
                        or float(raw_weight) <= 0
                    ):
                        raise ValueError("refinement serving metadata is invalid")
                    normalized_unit = dict(unit)
                    normalized_unit["unit"] = _sanitize_refinement_string(
                        unit["unit"], 100, allow_empty=False
                    )
                    if isinstance(unit.get("description"), str):
                        normalized_unit["description"] = _sanitize_refinement_string(
                            unit["description"], 100
                        )
                    normalized_units.append(normalized_unit)
                normalized_item[key] = normalized_units
                continue
            if isinstance(value, (dict, list)):
                raise ValueError("refinement contains unsupported nested content")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("refinement contains non-finite nutrition")
            if isinstance(value, str) and key in _REFINEMENT_STRING_LIMITS:
                normalized_item[key] = _sanitize_refinement_string(
                    value,
                    _REFINEMENT_STRING_LIMITS[key],
                    allow_empty=key not in _NON_EMPTY_REFINEMENT_STRING_KEYS,
                )
        validated.append(normalized_item)
    return validated


def _sanitize_refinement_string(
    value: str, max_length: int, *, allow_empty: bool = True
) -> str:
    """Keep refinement labels bounded and subject to the normal prompt guard."""
    stripped = value.strip()
    if len(stripped) > max_length:
        raise ValueError("refinement string exceeds the supported length")
    if not stripped and allow_empty:
        return ""
    if not stripped:
        raise ValueError("refinement string must not be empty")
    sanitized = sanitize_user_description(stripped)
    if sanitized is None:
        raise ValueError("refinement contains unsafe prompt text")
    return sanitized
