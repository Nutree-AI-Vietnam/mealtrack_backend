"""Publish one recipe payload and its validated projections together.

``content_hash`` is the catalog revision id stored on ``meal_catalog``. It is
not derived here and does not reconstruct a historical logged meal.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.domain.model.meal_recommendation.catalog_recipe import (
    normalize_catalog_ingredient_category,
)
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    matched_allergen_codes,
)
from src.domain.services.weekly_meal_planner.food_alias import resolve_food_alias

PAYLOAD_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IngredientProjection:
    """Nutrition-safe ingredient row written with the payload."""

    position: int
    food_reference_id: int | None
    display_name: str
    quantity: Decimal
    unit: str
    category: str = "pantry"
    raw_text: str | None = None
    quantity_text: str | None = None
    is_optional: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category",
            normalize_catalog_ingredient_category(self.category),
        )


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

    quantity = line.get("quantity")
    if quantity is None or quantity == "":
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
    nutrition: dict[str, Any] | None = None,
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
        food_ref_id = line.get("food_reference_id")
        projections.append(
            IngredientProjection(
                position=next_position,
                food_reference_id=int(food_ref_id) if food_ref_id is not None else None,
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
    if nutrition is not None:
        payload["nutrition"] = dict(nutrition)
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
                step_number=int(str(step["step"])),
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


# ---------------------------------------------------------------------------
# Checking a generated recipe
#
# A model writes a dish and its gram amounts; nothing it says about nutrition,
# allergens or safety is taken on trust. These rules run on the resolved
# ingredients before the recipe may be shown, and they decide three things:
# whether the recipe is usable at all, whether its nutrition can be computed,
# and whether it may be served to a user with a dietary constraint.
#
# Unknown is never treated as safe. An ingredient the food database could not
# identify makes the recipe ineligible for anyone with an allergy or a diet,
# at any weight, because a missing identification hides exactly the carriers
# that matter: fish sauce, shrimp paste, soy, sesame.
# ---------------------------------------------------------------------------

# Nutrition tolerates a small unidentified line; constraints do not.
UNRESOLVED_NUTRITION_LIMIT_G = 10.0
# Above this share of calories from AI-estimated values the recipe is flagged
# for review rather than served.
AI_ESTIMATE_KCAL_SHARE_LIMIT = 0.10
# A serving outside this range is not a meal.
MIN_SERVING_GRAMS = 120.0
MAX_SERVING_GRAMS = 1500.0

# A crawled recipe usually does not say how many it feeds, and it is written
# for the pot, not the plate. Scaling such a recipe by 1 drops a family dish
# whole into a one-person week, so its weighed ingredients estimate the
# servings instead.
#
# Only weight lines count. ingredient_quantity_normalization refuses to invent
# "1 quả cà chua = 120 g" and so does this: a recipe written in bó and quả
# gives no estimate rather than a confident wrong one. Liquids are left out
# too, so a canh is judged on its solids rather than on its broth.
SOLID_GRAMS_PER_SERVING = 250.0
MAX_ESTIMATED_SERVINGS = 8
# Under this the weighed lines are too small a share of the dish to divide by.
MIN_WEIGHED_GRAMS = 150.0

INGREDIENT_FROM_DATABASE = "food_reference"
INGREDIENT_FROM_PROVIDER = "provider"
INGREDIENT_AI_ESTIMATED = "ai_estimate"
INGREDIENT_UNRESOLVED = "unresolved"

# Dishes a generator must never produce. Eating these raw or fermented raw
# carries risks no recipe check can manage: Streptococcus suis from raw pig
# blood and fermented raw pork, liver flukes from raw freshwater fish,
# tetrodotoxin, and misidentified wild mushrooms.
_BLOCKED_DISHES = (
    "tiết canh",
    "nem chua",
    "gỏi cá",
    "cá sống",
    "pufferfish",
    "cá nóc",
    "wild mushroom",
    "nấm rừng",
    "nấm dại",
)
# Safe only when thoroughly boiled: cyanogenic glycosides in fresh bamboo
# shoots and cassava, lectins in raw kidney beans.
_MUST_BE_BOILED = (
    "măng tươi",
    "măng chua",
    "sắn",
    "khoai mì",
    "đậu tây",
    "kidney bean",
)
_BOILING_WORDS = ("luộc", "đun sôi", "nấu sôi", "boil", "simmer")
# Raw animal food needs a step that says it is cooked through.
_RAW_ANIMAL_WORDS = (
    "thịt heo",
    "thịt bò",
    "thịt gà",
    "gà",
    "vịt",
    "trứng",
    "tôm",
    "cua",
    "mực",
    "cá",
    "chicken",
    "pork",
    "beef",
    "egg",
    "shrimp",
    "fish",
)
_DONENESS_WORDS = (
    "chín",
    "chín kỹ",
    "chín đều",
    "luộc",
    "hấp",
    "kho",
    "rang",
    "chiên",
    "xào",
    "nướng",
    "đun sôi",
    "cooked through",
    "until cooked",
    "no longer pink",
    "boil",
    "steam",
    "fry",
    "grill",
    "roast",
    "simmer",
    "°c",
)
# Preparations a generated recipe may not ask for.
_RAW_PREPARATIONS = ("tái", "sống", "raw", "rare", "sashimi", "medium rare")
# A dish cooked in fat must account for the fat.
_FRYING_WORDS = ("chiên", "rán", "xào", "quay", "fry", "stir-fry", "deep-fry", "sauté")
_FAT_WORDS = ("dầu", "mỡ", "bơ", "oil", "butter", "lard", "ghee")


@dataclass(frozen=True)
class DraftIngredient:
    """One line of a generated recipe, after the resolver has run."""

    name: str
    grams: float
    source: str = INGREDIENT_UNRESOLVED
    food_reference_id: int | None = None
    kcal: float = 0.0
    # The food the resolver matched, to catch a raw line mapped to a cooked
    # food or the other way round.
    resolved_name: str | None = None


@dataclass(frozen=True)
class RecipeDraft:
    """What a generator produced, before anything is shown to a user."""

    name: str
    ingredients: tuple[DraftIngredient, ...]
    steps: tuple[str, ...] = ()
    servings: int = 1


@dataclass(frozen=True)
class DraftReview:
    """What may be done with a generated recipe."""

    rejections: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    # False when an unidentified ingredient could hide an allergen.
    constraints_checkable: bool = True

    @property
    def usable(self) -> bool:
        return not self.rejections

    @property
    def needs_review(self) -> bool:
        return bool(self.flags)

    def servable_to(self, *, has_dietary_constraint: bool) -> bool:
        """Whether this recipe may be put in front of a given user."""
        if not self.usable:
            return False
        return self.constraints_checkable or not has_dietary_constraint


def _mentions(text: str, words: Iterable[str]) -> bool:
    folded = unicodedata.normalize("NFC", text or "").casefold()
    return any(word in folded for word in words)


def _cooked_state(text: str) -> str | None:
    folded = unicodedata.normalize("NFC", text or "").casefold()
    if any(word in folded for word in ("cooked", "chín", "cơm")):
        return "cooked"
    if any(word in folded for word in ("raw", "sống", "gạo")):
        return "raw"
    return None


def estimate_servings_from_weight(weighed_grams: float) -> int | None:
    """How many people a recipe of this solid weight feeds, or None.

    None means the weighed lines say too little to divide by; the caller must
    then leave the recipe unscaled rather than guess a portion.
    """
    if weighed_grams < MIN_WEIGHED_GRAMS:
        return None
    servings = int(round(weighed_grams / SOLID_GRAMS_PER_SERVING))
    return max(1, min(servings, MAX_ESTIMATED_SERVINGS))


def review_recipe_draft(draft: RecipeDraft) -> DraftReview:
    """Apply every rule a generated recipe must pass before it is shown."""
    rejections: list[str] = []
    flags: list[str] = []
    steps_text = " ".join(draft.steps)
    whole_text = f"{draft.name} {steps_text} " + " ".join(
        line.name for line in draft.ingredients
    )

    if not draft.ingredients:
        rejections.append("recipe has no ingredients")
    if draft.servings < 1:
        rejections.append("recipe has no servings")

    # Food safety comes first: these are not fixable by re-resolving.
    if _mentions(whole_text, _BLOCKED_DISHES):
        rejections.append("dish is unsafe to generate")
    if _mentions(steps_text, _RAW_PREPARATIONS) or _mentions(
        draft.name, _RAW_PREPARATIONS
    ):
        rejections.append("recipe serves food raw or rare")
    for line in draft.ingredients:
        if _mentions(line.name, _MUST_BE_BOILED) and not _mentions(
            steps_text, _BOILING_WORDS
        ):
            rejections.append(f"{line.name} must be boiled thoroughly")
    if (
        draft.steps
        and _mentions(whole_text, _RAW_ANIMAL_WORDS)
        and not _mentions(steps_text, _DONENESS_WORDS)
    ):
        rejections.append("no step says the food is cooked through")
    if _mentions(whole_text, _FRYING_WORDS) and not _mentions(whole_text, _FAT_WORDS):
        rejections.append("a fried dish must list its cooking fat")

    # Nutrition: a large unidentified line makes the numbers meaningless.
    unresolved = [
        line
        for line in draft.ingredients
        if line.source in {INGREDIENT_UNRESOLVED, ""} or line.food_reference_id is None
    ]
    for line in unresolved:
        if line.grams > UNRESOLVED_NUTRITION_LIMIT_G:
            rejections.append(f"{line.name} could not be identified")

    estimated_kcal = sum(
        line.kcal
        for line in draft.ingredients
        if line.source == INGREDIENT_AI_ESTIMATED
    )
    total_kcal = sum(line.kcal for line in draft.ingredients)
    if total_kcal > 0 and estimated_kcal / total_kcal > AI_ESTIMATE_KCAL_SHARE_LIMIT:
        flags.append("too much of the energy is an AI estimate")

    for line in draft.ingredients:
        if line.resolved_name is None:
            continue
        wanted, matched = _cooked_state(line.name), _cooked_state(line.resolved_name)
        if wanted and matched and wanted != matched:
            rejections.append(f"{line.name} was matched to {line.resolved_name}")

    grams = sum(line.grams for line in draft.ingredients) / max(draft.servings, 1)
    if draft.ingredients and not MIN_SERVING_GRAMS <= grams <= MAX_SERVING_GRAMS:
        rejections.append(f"a serving weighs {grams:.0f} g")

    return DraftReview(
        rejections=tuple(dict.fromkeys(rejections)),
        flags=tuple(dict.fromkeys(flags)),
        # Any unidentified or estimated line can hide an allergen.
        constraints_checkable=not unresolved
        and all(line.source != INGREDIENT_AI_ESTIMATED for line in draft.ingredients),
    )
