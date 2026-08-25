"""Parse FatSecret search ``food_description`` strings into per-100g macros.

Search responses often include a description like:
``Per 100g - Calories: 155kcal | Fat: 11g | Carbs: 1.1g | Protein: 13g``

This is display-quality for list results. Authoritative per-100g + servings
still come from ``food.get.v5`` on select.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PER_100G_HINT = re.compile(r"per\s*100\s*g", re.IGNORECASE)


def parse_fatsecret_nutrition(food: dict[str, Any]) -> dict[str, float]:
    """Parse calories/protein/carbs/fat from a FatSecret food_description field."""
    desc = str(food.get("food_description") or "")
    if not desc:
        return {}
    result: dict[str, float] = {}
    try:
        for part in desc.split("|"):
            part = part.strip().lower()
            if "calories" in part or (
                "cal" in part and "carb" not in part and "protein" not in part
            ):
                val = re.search(r"(?:calories|cal(?:ories)?)\s*[:=]?\s*([\d.]+)", part)
                if not val:
                    val = re.search(r"([\d.]+)\s*kcal", part)
                if val:
                    result["calories"] = float(val.group(1))
            elif "fat" in part:
                val = re.search(r"fat\s*[:=]?\s*([\d.]+)", part)
                if not val:
                    val = re.search(r"([\d.]+)", part)
                if val:
                    result["fat"] = float(val.group(1))
            elif "carb" in part:
                val = re.search(r"carb(?:ohydrate|s)?\s*[:=]?\s*([\d.]+)", part)
                if not val:
                    val = re.search(r"([\d.]+)", part)
                if val:
                    result["carbs"] = float(val.group(1))
            elif "protein" in part:
                val = re.search(r"protein\s*[:=]?\s*([\d.]+)", part)
                if not val:
                    val = re.search(r"([\d.]+)", part)
                if val:
                    result["protein"] = float(val.group(1))
    except Exception as exc:
        logger.debug("Could not parse fatsecret nutrition: %s", type(exc).__name__)
        return {}
    return result


def description_macros_as_100g(food: dict[str, Any]) -> dict[str, float]:
    """Map description macros onto ``*_100g`` fields when description is per-100g.

    Non-100g descriptions (e.g. ``Per Serving``) are ignored so we do not
    treat serving-basis values as per-100g densities.
    """
    desc = str(food.get("food_description") or "")
    if not desc or not _PER_100G_HINT.search(desc):
        return {}
    parsed = parse_fatsecret_nutrition(food)
    if not parsed:
        return {}
    mapped: dict[str, float] = {}
    if "calories" in parsed:
        mapped["calories_100g"] = parsed["calories"]
    if "protein" in parsed:
        mapped["protein_100g"] = parsed["protein"]
    if "carbs" in parsed:
        mapped["carbs_100g"] = parsed["carbs"]
    if "fat" in parsed:
        mapped["fat_100g"] = parsed["fat"]
    return mapped
