"""Publish one recipe payload and its validated projections together.

``content_hash`` is the catalog revision id stored on ``meal_catalog``. It is
not derived here and does not reconstruct a historical logged meal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from src.domain.services.weekly_meal_planner.allergen_constraint import (
    matched_allergen_codes,
)
from src.domain.services.weekly_meal_planner.food_alias import resolve_food_alias

PAYLOAD_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IngredientProjection:
    """Nutrition-safe ingredient row written with the payload."""

    position: int
    food_reference_id: int
    display_name: str
    quantity: Decimal
    unit: str
    category: str = "pantry"
    raw_text: str | None = None
    quantity_text: str | None = None
    is_optional: bool = False


@dataclass(frozen=True)
class StepProjection:
    step_number: int
    title: str
    description: str


@dataclass(frozen=True)
class PublishedRecipe:
    recipe_payload: dict
    payload_schema_version: int
    payload_digest: str
    publication_status: str
    nutrition_status: str
    ingredients: tuple[IngredientProjection, ...]
    steps: tuple[StepProjection, ...]
    allergen_codes: tuple[str, ...] = ()

    @property
    def planner_eligible(self) -> bool:
        return is_planner_eligible(
            publication_status=self.publication_status,
            nutrition_status=self.nutrition_status,
        )


def is_planner_eligible(
    *,
    publication_status: str,
    nutrition_status: str,
    is_active: bool = True,
) -> bool:
    """Active planner and grocery recipes are published and nutrition-ready."""

    return (
        bool(is_active)
        and publication_status == "published"
        and nutrition_status == "ready"
    )


def payload_digest(payload: Mapping) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_nutrition_safe_ingredient(line: Mapping) -> bool:
    """Resolved, positive quantities are the only grocery and nutrition inputs."""

    food_id = line.get("food_reference_id")
    quantity = line.get("quantity")
    if food_id is None or quantity is None or quantity == "":
        return False
    try:
        amount = Decimal(str(quantity))
    except Exception:
        return False
    return amount > 0


def projection_nutrition_quantity(line: Mapping) -> Decimal:
    if not is_nutrition_safe_ingredient(line):
        return Decimal("0")
    return Decimal(str(line.get("quantity")))


def publish_recipe(
    *,
    recipe_name: str,
    description: str | None,
    ingredients: list[Mapping],
    instructions: list[Mapping],
    nutrition_ready: bool,
    publish: bool = True,
    tags: list[str] | None = None,
    equipment: list[str] | None = None,
    allergen_disclosures: list[str] | None = None,
    source: Mapping | None = None,
    aliases: list[tuple[str, int]] | None = None,
    known_allergen_codes: list[str] | None = None,
) -> PublishedRecipe:
    """Build the canonical payload and the normalized rows from one input."""

    alias_rows = tuple(aliases or ())
    payload_ingredients: list[dict] = []
    projections: list[IngredientProjection] = []
    next_position = 1
    for raw in ingredients:
        line = dict(raw)
        if line.get("food_reference_id") is None and line.get("name"):
            resolved = resolve_food_alias(str(line["name"]), alias_rows)
            if resolved is not None:
                line["food_reference_id"] = resolved
        payload_ingredients.append(
            {
                "name": line.get("name"),
                "quantity": _json_quantity(line.get("quantity")),
                "unit": line.get("unit"),
                "raw_text": line.get("raw_text"),
                "quantity_text": line.get("quantity_text"),
                "food_reference_id": line.get("food_reference_id"),
            }
        )
        if not is_nutrition_safe_ingredient(line):
            continue
        projections.append(
            IngredientProjection(
                position=next_position,
                food_reference_id=int(line["food_reference_id"]),
                display_name=str(line.get("name") or line.get("display_name") or ""),
                quantity=Decimal(str(line["quantity"])),
                unit=str(line.get("unit") or ""),
                category=str(line.get("category") or "pantry"),
                raw_text=_optional_text(line.get("raw_text")),
                quantity_text=_optional_text(line.get("quantity_text")),
                is_optional=bool(line.get("is_optional", False)),
            )
        )
        next_position += 1

    payload_steps = [
        {
            "step": int(step.get("step") or step.get("step_number") or index),
            "title": step.get("title"),
            "instruction": step.get("instruction") or step.get("description") or "",
        }
        for index, step in enumerate(instructions, start=1)
    ]
    payload = {
        "recipe_name": recipe_name,
        "description": description,
        "ingredients": payload_ingredients,
        "instructions": payload_steps,
        "tags": list(tags or []),
        "equipment": list(equipment or []),
        "allergen_disclosures": list(allergen_disclosures or []),
        "source": dict(source or {}),
    }
    publication_status = "published" if publish else "draft"
    nutrition_status = "ready" if nutrition_ready else "not_ready"
    return PublishedRecipe(
        recipe_payload=payload,
        payload_schema_version=PAYLOAD_SCHEMA_VERSION,
        payload_digest=payload_digest(payload),
        publication_status=publication_status,
        nutrition_status=nutrition_status,
        ingredients=tuple(projections),
        steps=tuple(
            StepProjection(
                step_number=int(step["step"]),
                title=str(step.get("title") or f"Step {step['step']}"),
                description=str(step["instruction"]),
            )
            for step in payload_steps
            if str(step["instruction"]).strip()
        ),
        allergen_codes=matched_allergen_codes(
            allergen_disclosures or [], known_allergen_codes or []
        ),
    )


def _json_quantity(value: object) -> int | float | None:
    if value is None or value == "":
        return None
    amount = Decimal(str(value))
    if amount == amount.to_integral():
        return int(amount)
    return float(amount)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
