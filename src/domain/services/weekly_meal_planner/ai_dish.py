"""A dish the planner asked an AI to invent, on its way into a slot.

The weekly plan used to fill its slots from ``meal_catalog``. The catalog is
crawled, so a slot could end up holding a blog headline — "Vét hết nồi cơm
với món vịt kho thơm" — and the same row could win every day of the week.
The catalog's job is to be reference material and a place a user keeps their
own dishes, not the thing a plan hands back.

So the planner asks for dishes instead, and this is what comes back before
anything is stored: one dish, checked. A dish only reaches a slot when it is
safe to cook, its macros could describe food, and no other slot in the week
already holds it.

Nothing here talks to a model or a database. The caller brings the answer,
this says whether the week may keep it.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.plausibility import per_100g_problem
from src.domain.model.weekly_meal_planner.saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
    SavedRecipeStep,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    DraftIngredient,
    RecipeDraft,
    review_recipe_safety,
)

# An AI dish is written for one person, so the snapshot is one serving.
AI_DISH_SERVINGS = 1
# Marks a slot's snapshot as invented for this plan rather than saved by the
# user, so groceries, logging and the app can tell them apart.
SOURCE_PLANNER_AI = "planner_ai"

# A plate of food lives inside these. Outside them something was misread.
MIN_DISH_KCAL = 80.0
MAX_DISH_KCAL = 1_600.0
MIN_DISH_INGREDIENTS = 2
MIN_DISH_STEPS = 1


@dataclass(frozen=True)
class RejectedDish:
    """Why a dish the model wrote cannot go in a slot."""

    name: str
    reasons: tuple[str, ...]


def dish_key(name: str) -> str:
    """What makes two dish names the same dish, for a week's variety.

    Accents and case go, so "Cá Kho Tộ" and "ca kho to" are one dish, and a
    model that answers the same thing twice in different dress is caught.
    """
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFKD", name or "")
        if not unicodedata.combining(char)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).split())


def review_ai_dish(
    snapshot: SavedRecipeSnapshot,
    *,
    taken: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Reasons this dish cannot take a slot, or nothing when it can.

    ``taken`` holds the keys of dishes the week already serves, so a model
    that repeats itself does not fill the week with one meal — the complaint
    that sent the planner here in the first place.
    """
    reasons: list[str] = []
    name = (snapshot.name or "").strip()
    if not name:
        reasons.append("UNNAMED")
    elif dish_key(name) in taken:
        reasons.append("ALREADY_IN_THE_WEEK")
    if len(snapshot.ingredients) < MIN_DISH_INGREDIENTS:
        reasons.append("TOO_FEW_INGREDIENTS")
    if len(snapshot.steps) < MIN_DISH_STEPS:
        reasons.append("NO_STEPS")

    kcal = float(snapshot.calories)
    if not MIN_DISH_KCAL <= kcal <= MAX_DISH_KCAL:
        reasons.append("IMPLAUSIBLE_CALORIES")

    grams = sum(line.grams or 0.0 for line in snapshot.ingredients)
    if grams > 0:
        factor = 100.0 / grams
        problem = per_100g_problem(
            snapshot.protein * factor,
            snapshot.carbs * factor,
            snapshot.fat * factor,
            snapshot.fiber * factor,
        )
        if problem:
            reasons.append(f"IMPLAUSIBLE_MACROS_{problem.upper()}")

    reasons.extend(review_recipe_safety(_draft_of(snapshot)))
    return tuple(reasons)


def _draft_of(snapshot: SavedRecipeSnapshot) -> RecipeDraft:
    """The snapshot in the shape the safety reviewer reads."""
    return RecipeDraft(
        name=snapshot.name,
        ingredients=tuple(
            DraftIngredient(name=line.name, grams=float(line.grams or 0.0))
            for line in snapshot.ingredients
        ),
        steps=tuple(step.instruction for step in snapshot.steps),
        servings=max(1, int(snapshot.servings or 1)),
    )


def snapshot_from_ai_recipe(
    payload: dict,
    *,
    dish_id: str,
    language: str | None = None,
) -> SavedRecipeSnapshot | None:
    """One generated recipe as a slot snapshot, or None when unreadable.

    Macros are summed from the ingredient lines rather than taken from any
    total the model volunteered, and calories are never read at all: the
    snapshot derives them from the macros, as every logged meal does.
    """
    name = str(payload.get("name") or "").strip()
    lines = payload.get("ingredients")
    if not name or not isinstance(lines, list):
        return None

    ingredients: list[SavedRecipeIngredient] = []
    protein = carbs = fat = fiber = sugar = 0.0
    for raw in lines:
        if not isinstance(raw, dict):
            continue
        line_name = str(raw.get("name") or "").strip()
        if not line_name:
            continue
        grams = _number(raw.get("grams"))
        if grams is None:
            grams = _number(raw.get("amount")) if _is_grams(raw.get("unit")) else None
        ingredients.append(
            SavedRecipeIngredient(
                name=line_name,
                amount=_number(raw.get("amount")),
                unit=str(raw.get("unit") or ""),
                grams=grams,
                protein=_number(raw.get("protein")),
                carbs=_number(raw.get("carbs")),
                fat=_number(raw.get("fat")),
                fiber=_number(raw.get("fiber")),
                sugar=_number(raw.get("sugar")),
            )
        )
        protein += _number(raw.get("protein")) or 0.0
        carbs += _number(raw.get("carbs")) or 0.0
        fat += _number(raw.get("fat")) or 0.0
        fiber += _number(raw.get("fiber")) or 0.0
        sugar += _number(raw.get("sugar")) or 0.0

    if not ingredients:
        return None

    return SavedRecipeSnapshot(
        suggestion_id=dish_id,
        name=name,
        servings=AI_DISH_SERVINGS,
        protein=round(protein, 2),
        carbs=round(carbs, 2),
        fat=round(fat, 2),
        fiber=round(fiber, 2),
        sugar=round(sugar, 2),
        ingredients=tuple(ingredients),
        steps=tuple(_steps_of(payload)),
        cook_time_minutes=_whole(payload.get("prep_time_minutes")),
        emoji=_short(payload.get("emoji")),
        cuisine_type=_short(payload.get("cuisine_type")),
        origin_country=_short(payload.get("origin_country")),
        language=language,
        source=SOURCE_PLANNER_AI,
    )


def _steps_of(payload: dict) -> list[SavedRecipeStep]:
    steps: list[SavedRecipeStep] = []
    for raw in payload.get("recipe_steps") or payload.get("steps") or []:
        if isinstance(raw, str):
            text = raw.strip()
            duration = None
        elif isinstance(raw, dict):
            text = str(raw.get("instruction") or raw.get("description") or "").strip()
            duration = _whole(raw.get("duration_minutes"))
        else:
            continue
        if text:
            steps.append(SavedRecipeStep(instruction=text, duration_minutes=duration))
    return steps


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _whole(value: object) -> int | None:
    number = _number(value)
    return int(round(number)) if number is not None else None


def _is_grams(unit: object) -> bool:
    return str(unit or "").strip().casefold() in {"g", "gram", "grams", "ml"}


def _short(value: object, limit: int = 50) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def dish_calories(snapshot: SavedRecipeSnapshot) -> float:
    """Per-serving calories, from the macros rather than anything claimed."""
    return Macros.raw_total_calories(
        snapshot.protein, snapshot.carbs, snapshot.fat, snapshot.fiber
    )


# ---------------------------------------------------------------------------
# Asking for a week of dishes
# ---------------------------------------------------------------------------

# Names come back in one call per meal type, so a week is three cheap calls
# plus one per slot for the recipe itself.
DISH_NAME_SYSTEM = (
    "You are a creative chef. Generate diverse meal names with different cuisines."
)
DISH_RECIPE_SYSTEM = (
    "You are a professional chef. Generate complete recipe details as valid JSON."
)
# A rejected dish gets one replacement; a model that writes an unsafe recipe
# twice for the same slot is not going to write a safe one on the third try.
MAX_DISH_ATTEMPTS = 2
# How many reference dishes are worth showing. More is noise, and the
# catalogue is crawled, so these are a hint and never an instruction.
MAX_REFERENCE_DISHES = 8


@dataclass(frozen=True)
class DishRequest:
    """One slot to fill, and what it should come to."""

    day_index: int
    slot_index: int
    meal_type: str
    target_calories: int
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None

    @property
    def slot(self) -> tuple[int, int]:
        return (self.day_index, self.slot_index)


@dataclass(frozen=True)
class WeekOfDishes:
    """What came back: a dish per slot, and what was turned away."""

    dishes: dict[tuple[int, int], SavedRecipeSnapshot]
    rejected: tuple[RejectedDish, ...] = ()
    unfilled: tuple[tuple[int, int], ...] = ()


def reference_block(names: tuple[str, ...]) -> str:
    """The catalogue's dishes as a hint, which is all they are.

    The catalogue is crawled, so its rows carry blog headlines and noise. It
    is shown so a dish lands in the right kitchen, never copied: the plan
    hands back what the model writes, not a catalogue row.
    """
    usable = [name.strip() for name in names if name and name.strip()]
    if not usable:
        return ""
    listed = ", ".join(usable[:MAX_REFERENCE_DISHES])
    return (
        f"\nDishes this kitchen already cooks, for flavour and style only: {listed}."
        "\nWrite your own dish. Do not reuse these names."
    )


def _memory_block(text: str) -> str:
    stripped = (text or "").strip()
    return f"\nWhat this person eats:\n{stripped}" if stripped else ""


async def generate_week_of_dishes(
    requests: tuple[DishRequest, ...],
    *,
    ask,
    build_names_prompt,
    build_recipe_prompt,
    reference_dishes: tuple[str, ...] = (),
    memory_text: str = "",
    cuisine_region: str | None = None,
    language: str | None = None,
    new_id=None,
) -> WeekOfDishes:
    """Fill every slot with a dish the model wrote, each one checked.

    ``ask`` runs one prompt and returns the parsed answer; everything that
    talks to a model lives behind it, so this can be read and tested on its
    own. A slot nobody could fill is reported rather than quietly left
    holding something unsafe or a repeat of another day.
    """
    mint = new_id or (lambda: uuid.uuid4().hex)
    dishes: dict[tuple[int, int], SavedRecipeSnapshot] = {}
    rejected: list[RejectedDish] = []
    unfilled: list[tuple[int, int]] = []
    taken: set[str] = set()
    hint = reference_block(reference_dishes) + _memory_block(memory_text)

    by_meal: dict[str, list[DishRequest]] = {}
    for request in requests:
        by_meal.setdefault(request.meal_type, []).append(request)

    for meal_type, slots in by_meal.items():
        names = await _dish_names(
            ask=ask,
            build_names_prompt=build_names_prompt,
            meal_type=meal_type,
            slots=slots,
            hint=hint,
            cuisine_region=cuisine_region,
            taken=taken,
        )
        for index, request in enumerate(slots):
            snapshot = await _dish_for_slot(
                request,
                ask=ask,
                build_recipe_prompt=build_recipe_prompt,
                names=names[index:],
                cuisine_region=cuisine_region,
                language=language,
                taken=taken,
                rejected=rejected,
                mint=mint,
            )
            if snapshot is None:
                unfilled.append(request.slot)
                continue
            dishes[request.slot] = snapshot
            taken.add(dish_key(snapshot.name))

    return WeekOfDishes(
        dishes=dishes, rejected=tuple(rejected), unfilled=tuple(unfilled)
    )


async def _dish_names(
    *, ask, build_names_prompt, meal_type, slots, hint, cuisine_region, taken
) -> list[str]:
    """One call for this meal type's names, in the order the slots want them."""
    prompt = build_names_prompt(
        meal_type=meal_type,
        target_calories=int(slots[0].target_calories),
        count=len(slots),
        cuisine_region=cuisine_region,
        exclude_meal_names=sorted(taken) or None,
    )
    try:
        answer = await ask(prompt + hint, DISH_NAME_SYSTEM)
    except Exception:  # noqa: BLE001 - a slot can still ask for its own dish
        return []
    return _names_of(answer)


def _names_of(answer) -> list[str]:
    items = answer.get("meals") if isinstance(answer, dict) else answer
    names: list[str] = []
    for item in items or []:
        name = item if isinstance(item, str) else (item or {}).get("name")
        text = str(name or "").strip()
        if text:
            names.append(text)
    return names


async def _dish_for_slot(
    request: DishRequest,
    *,
    ask,
    build_recipe_prompt,
    names: list[str],
    cuisine_region,
    language,
    taken: set[str],
    rejected: list[RejectedDish],
    mint,
) -> SavedRecipeSnapshot | None:
    """The dish this slot keeps, after at most MAX_DISH_ATTEMPTS tries."""
    candidates = [name for name in names if dish_key(name) not in taken]
    if names and not candidates:
        # Every name offered is already on the table this week. Asking again
        # with no name invites the model to repeat itself a third time.
        rejected.append(RejectedDish(name=names[0], reasons=("ALREADY_IN_THE_WEEK",)))
        return None
    for attempt in range(MAX_DISH_ATTEMPTS):
        name = candidates[attempt] if attempt < len(candidates) else None
        prompt = build_recipe_prompt(
            meal_name=name or "",
            meal_type=request.meal_type,
            target_calories=int(request.target_calories),
            cuisine_region=cuisine_region,
            protein_target=request.protein,
            carbs_target=request.carbs,
            fat_target=request.fat,
        )
        try:
            payload = await ask(prompt, DISH_RECIPE_SYSTEM)
        except Exception:  # noqa: BLE001 - one failed call is not the week
            continue
        if not isinstance(payload, dict):
            continue
        payload.setdefault("name", name or "")
        snapshot = snapshot_from_ai_recipe(
            payload, dish_id=f"ai_{mint()}", language=language
        )
        if snapshot is None:
            rejected.append(RejectedDish(name=str(name or ""), reasons=("UNREADABLE",)))
            continue
        reasons = review_ai_dish(snapshot, taken=frozenset(taken))
        if not reasons:
            return snapshot
        rejected.append(RejectedDish(name=snapshot.name, reasons=reasons))
    return None
