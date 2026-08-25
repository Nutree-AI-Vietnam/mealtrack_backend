"""
Utility functions for meal text parsing.
Extracted from parse_meal_text_handler.py for better organization.
"""

import json
import logging
import re
from typing import Any

from src.domain.services.fatsecret_description_nutrition import (
    parse_fatsecret_nutrition,
)

logger = logging.getLogger(__name__)

__all__ = [
    "extract_json_from_response",
    "extract_usda_nutrition",
    "parse_fatsecret_nutrition",
]


def extract_json_from_response(content: str) -> list[dict[str, Any]]:
    """
    Extract JSON array from AI response.

    Args:
        content: The raw response string from the AI

    Returns:
        List of parsed food items

    Raises:
        ValueError: If JSON cannot be extracted
    """
    # Try to parse the entire response as JSON
    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
        return [result]
    except json.JSONDecodeError:
        pass

    # Try to find JSON in markdown code block
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if json_match:
        try:
            result = json.loads(json_match.group(1).strip())
            if isinstance(result, list):
                return result
            return [result]
        except json.JSONDecodeError:
            pass

    # Try to find JSON array (non-greedy to handle multiple bracket groups)
    json_match = re.search(r"\[[\s\S]*?\]", content)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try stripping leading/trailing non-JSON chars and re-parse
    stripped = content.strip()
    if stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # Try finding the array after stripping
    match = re.search(r"\[[\s\S]*\]", stripped)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Could not extract JSON from AI response: length=%s", len(content))
    raise ValueError("Could not parse AI response. Please try again.")


def extract_usda_nutrition(nutrients: list[dict[str, Any]]) -> dict[str, float]:
    """Extract nutrition values from USDA nutrients list."""
    result = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    for n in nutrients:
        # USDA format: {"nutrient": {"id": 1008}, "amount": 165.0}
        nutrient_info = n.get("nutrient", {})
        nutrient_id = nutrient_info.get("id")
        value = n.get("amount", 0)
        if nutrient_id in (208, 1008):  # Energy
            result["calories"] = float(value)
        elif nutrient_id in (203, 1003):  # Protein
            result["protein"] = float(value)
        elif nutrient_id in (205, 1005):  # Carbs
            result["carbs"] = float(value)
        elif nutrient_id in (204, 1004):  # Fat
            result["fat"] = float(value)
    return result
