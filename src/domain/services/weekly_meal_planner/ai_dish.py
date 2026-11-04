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
from dataclasses import dataclass, replace

from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.plausibility import per_100g_problem
from src.domain.model.weekly_meal_planner.saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
    SavedRecipeStep,
)
from src.domain.services.prompts.system_prompts import SystemPrompts
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

    Word order goes too. Asked for a varied week the model answered "Canh
    gừng đậu hũ" and "Canh đậu hũ gừng" — one soup, named twice, and both
    passed an exact-name check. Sorting the words catches that, and the only
    names it merges are permutations of the same words, which name the same
    dish in Vietnamese as in English.
    """
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFKD", name or "")
        if not unicodedata.combining(char)
    )
    words = re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).split()
    return " ".join(sorted(words))


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
    # The model answers with "name" most of the time and "dish_name" the
    # rest, and a recipe whose name landed under the other key used to be
    # thrown away whole. "local_name" is the one asked for when the week is
    # written in the reader's language: the prompt pins the English working
    # name hard enough that asking the model to replace it only worked some
    # runs, so it is asked for a new field instead and that field wins.
    name = str(
        payload.get("local_name")
        or payload.get("name")
        or payload.get("dish_name")
        or ""
    ).strip()
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
                display_name=str(raw.get("local_name") or "").strip() or None,
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


def priced_snapshot(snapshot: SavedRecipeSnapshot, priced) -> SavedRecipeSnapshot:
    """The dish with the macros the backend worked out for its ingredients.

    A generated recipe carries grams and nothing else — the recipe prompt
    asks for no macros on purpose, because the backend prices every
    ingredient through food_reference, FatSecret and, failing both, an
    estimate. Without this step a dish sums to nothing and is turned away
    for having no calories, which is how a week came back empty.

    ``priced`` is what NutritionLookupService.calculate_meal_macros returns.
    """
    by_name = {
        str(line.name).strip().casefold(): line
        for line in getattr(priced, "ingredients", []) or []
    }
    lines = tuple(
        _with_macros(line, by_name.get(str(line.name).strip().casefold()))
        for line in snapshot.ingredients
    )
    return replace(
        snapshot,
        ingredients=lines,
        protein=round(float(getattr(priced, "protein", 0.0) or 0.0), 2),
        carbs=round(float(getattr(priced, "carbs", 0.0) or 0.0), 2),
        fat=round(float(getattr(priced, "fat", 0.0) or 0.0), 2),
        fiber=round(float(getattr(priced, "fiber", 0.0) or 0.0), 2),
        sugar=round(float(getattr(priced, "sugar", 0.0) or 0.0), 2),
    )


def _with_macros(line: SavedRecipeIngredient, priced) -> SavedRecipeIngredient:
    if priced is None:
        return line
    return replace(
        line,
        grams=line.grams
        if line.grams is not None
        else getattr(priced, "quantity_g", None),
        protein=getattr(priced, "protein", None),
        carbs=getattr(priced, "carbs", None),
        fat=getattr(priced, "fat", None),
        fiber=getattr(priced, "fiber", None),
        sugar=getattr(priced, "sugar", None),
        food_reference_id=getattr(priced, "food_reference_id", None),
    )


def pricing_lines(snapshot: SavedRecipeSnapshot) -> list[dict]:
    """The dish's ingredients in the shape the nutrition lookup reads."""
    return [
        {
            "name": line.name,
            "amount": line.grams if line.grams is not None else (line.amount or 0),
            "unit": "g" if line.grams is not None else (line.unit or "g"),
        }
        for line in snapshot.ingredients
    ]


# ---------------------------------------------------------------------------
# Asking for a week of dishes
# ---------------------------------------------------------------------------

# Names come back in one call per meal type, so a week is three cheap calls
# plus one per slot for the recipe itself.
# The discovery prompt describes the meals but never asks for JSON: the
# system prompt is what does that, and without it the model answers in
# markdown prose and every name is lost. The suggestion path has always
# paired the two; this one did not, and a whole week came back empty.
DISH_NAME_SYSTEM = SystemPrompts.DISCOVERY_SYSTEM
DISH_RECIPE_SYSTEM = (
    "You are a professional chef. Generate complete recipe details as valid JSON."
)
# A rejected dish gets one replacement; a model that writes an unsafe recipe
# twice for the same slot is not going to write a safe one on the third try.
MAX_DISH_ATTEMPTS = 2
# Names asked for beyond the slots that need them. One call writes a meal
# type's names and the last slot gets whatever is left, so a single repeat
# earlier in the list used to leave it with nothing and the day short a
# dish. Two spares cost about twenty tokens and give every slot a fallback.
NAME_SPARES = 2
# A dish turned away only for repeating the day's protein buys its slot one
# more try. Preferring a second protein is not the same as failing: counted
# against the ordinary budget it cost two dinners their dish outright, which
# is the trade this rule exists to avoid.
PROTEIN_RETRY_BONUS = 1
# How many reference dishes are worth showing. More is noise, and the
# catalogue is crawled, so these are a hint and never an instruction.
MAX_REFERENCE_DISHES = 8


# The protein a dish is built on, as the ingredient names say it. These are
# the English pricing keys, so the list stays English however the week reads.
# "chicken thigh" and "chicken breast" are one protein: a day that eats both
# ate chicken twice, whatever the two dishes were called.
PROTEIN_FAMILIES = (
    "chicken",
    "pork",
    "beef",
    "duck",
    "turkey",
    "lamb",
    "salmon",
    "tuna",
    "mackerel",
    "fish",
    "shrimp",
    "prawn",
    "squid",
    "crab",
    "clam",
    "tofu",
    "tempeh",
    "egg",
    "lentil",
    "chickpea",
    "bean",
)


def main_protein(snapshot: SavedRecipeSnapshot) -> str:
    """What this dish is built on, or "" when nothing stands out.

    Read off the priced lines rather than the dish name: the name is the
    model's prose and may be in any language, while the lines carry real
    grams of protein against canonical English names.
    """
    best, most = "", 0.0
    for line in snapshot.ingredients:
        grams = float(line.protein or 0.0)
        if grams > most:
            best, most = str(line.name or "").casefold(), grams
    if not best:
        return ""
    return next((family for family in PROTEIN_FAMILIES if family in best), best)


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
    # What this slot is in the user's own region, from the locale pack. The
    # model knows what "breakfast" means in general and gets it wrong here:
    # asked only for a 450 kcal breakfast it wrote seven cooked rice plates,
    # grilled chicken at 7am included. The locale already knew better — it
    # serves no rice at breakfast — and nothing was passing that on.
    note: str = ""
    # Minutes this slot is worth, when the region says less than the default.
    minutes: int | None = None

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


def _day_so_far_block(eaten: tuple[str, ...]) -> str:
    """What this day already holds, so the next meal is not the same again."""
    usable = [item for item in eaten if item]
    if not usable:
        return ""
    return (
        f"\nEarlier today this person eats: {', '.join(usable)}."
        "\nBuild this meal on a different main protein."
    )


def _slot_note_block(note: str) -> str:
    """What this slot is in the user's region, for the prompt."""
    stripped = (note or "").strip()
    return f"\n{stripped}" if stripped else ""


def _memory_block(text: str) -> str:
    stripped = (text or "").strip()
    return f"\nWhat this person eats:\n{stripped}" if stripped else ""


# The shared recipe prompt says it always answers in English, because the
# suggestion path translates afterwards from text prepared at catalog import.
# A dish invented here has no such prepared text, so nothing would translate
# it and a Vietnamese user would read an English week. Ask for the language
# up front instead.
LANGUAGE_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
}


def language_block(language: str | None) -> str:
    """Write the dish in the user's own language, since nothing translates it.

    The ingredient names stay English on purpose. They are the key the
    backend prices a line by: ``normalize_food_name`` strips every character
    outside ``a-z0-9``, so "thịt gà" normalises to "th g", misses
    food_reference and FatSecret alike, and every line falls through to the
    AI estimate. The grocery list still reads Vietnamese, because it shows
    ``food_reference.name_vi`` for the row the pricing resolved.
    """
    code = (language or "").strip().casefold()[:2]
    name = LANGUAGE_NAMES.get(code)
    if not name or code == "en":
        return ""
    return (
        f"\nWrite every step in {name}, using the everyday {name} words a home"
        " cook would say."
        f'\nAdd a "local_name" field holding the dish name in {name} — the name'
        " a home cook would call this dish, not a word-for-word translation."
        '\nKeep each ingredient\'s "name" in plain English, and give each one a'
        f' "local_name" holding what that ingredient is called in {name}.'
    )


# The names call stays in English, and the recipe call writes the Vietnamese
# name the week keeps. This is measured, not a preference: asked in English
# the model answered chicken, then fish, then tofu-and-pork; asked in
# Vietnamese it answered "Cơm gà xào sả", "Cơm gà sả ớt" and "Cơm gà sả
# gừng" — three names, one dish, every exact-name check passed. The English
# working name is a scaffold nobody sees; what reaches a slot is whatever
# the recipe call wrote.
#
# One discovery call writes a whole meal type's names at once, so excluding
# names already taken does nothing within that call: the model answered
# "Cơm gà xối mỡ", "Cơm gà sả nướng" and "Cơm gà nướng sả" in one breath and
# every one passed the exact-name check. Variety has to be asked for.
VARIETY_RULE = (
    "\n- Every name must use a different main protein and a different cooking"
    " method from the others in this list."
)


# A slot whose names were all used asks for a recipe with an empty dish
# name, and the model answers with an empty name too — a dish that cooks
# fine and is then thrown away for having no name. Ask it to name its own.
NAME_IT_YOURSELF_RULE = (
    '\nNo dish name was given. Invent one and return it in the "name" field;'
    " never leave that field empty."
)

# The safety reviewer turns away a fried dish that lists no cooking fat. The
# model writes one often enough to cost a slot, and it only does so because
# nothing told it the oil counts as an ingredient.
COOKING_FAT_RULE = (
    "\nIf the dish is fried, stir-fried or sauteed, list the cooking oil or fat"
    " as an ingredient with its amount in grams."
)


async def generate_week_of_dishes(
    requests: tuple[DishRequest, ...],
    *,
    ask,
    build_names_prompt,
    build_recipe_prompt,
    price=None,
    reference_dishes: tuple[str, ...] = (),
    memory_text: str = "",
    cuisine_region: str | None = None,
    language: str | None = None,
    new_id=None,
) -> WeekOfDishes:
    """Fill every slot with a dish the model wrote, each one checked.

    ``ask`` runs one prompt and returns the parsed answer and ``price``
    works out what a dish's ingredients come to; everything that talks to a
    model or a database lives behind them, so this can be read and tested on
    its own. Without ``price`` a dish keeps whatever macros the model
    volunteered, which for the recipe prompt is none. A slot nobody could fill is reported rather than quietly left
    holding something unsafe or a repeat of another day.
    """
    mint = new_id or (lambda: uuid.uuid4().hex)
    dishes: dict[tuple[int, int], SavedRecipeSnapshot] = {}
    rejected: list[RejectedDish] = []
    unfilled: list[tuple[int, int]] = []
    taken: set[str] = set()
    # Every English working name handed to the model, so the next meal
    # type's call can be told not to write them again. ``taken`` holds the
    # stored names, which are Vietnamese for a Vietnamese week, so it cannot
    # do this job.
    offered: set[str] = set()
    # The working names a slot actually spent. A slot used to be able to ask
    # for a name another slot had already cooked — the candidate filter
    # compared English working names against Vietnamese stored ones and so
    # never matched anything — and the attempt was lost to
    # ALREADY_IN_THE_WEEK before a different name was ever tried.
    used: set[str] = set()
    # Per day, the main protein of every dish already placed, and the names
    # of those dishes. Meal types are written one at a time — every
    # breakfast, then every lunch — so without this a day's dinner knows
    # nothing of its own lunch, and the week served "Cơm xào bò húng quế"
    # at noon and "Cơm áp chảo bò húng quế" at night.
    day_proteins: dict[int, set[str]] = {}
    day_dishes: dict[int, list[str]] = {}
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
            taken=taken | offered,
        )
        offered.update(dish_key(name) for name in names)
        for index, request in enumerate(slots):
            snapshot = await _dish_for_slot(
                request,
                ask=ask,
                build_recipe_prompt=build_recipe_prompt,
                names=names[index:],
                used=used,
                price=price,
                cuisine_region=cuisine_region,
                language=language,
                taken=taken,
                eaten_today=tuple(day_dishes.get(request.day_index, ())),
                proteins_today=frozenset(day_proteins.get(request.day_index, ())),
                rejected=rejected,
                mint=mint,
            )
            if snapshot is None:
                unfilled.append(request.slot)
                continue
            dishes[request.slot] = snapshot
            taken.add(dish_key(snapshot.name))
            day_dishes.setdefault(request.day_index, []).append(snapshot.name)
            protein = main_protein(snapshot)
            if protein:
                day_proteins.setdefault(request.day_index, set()).add(protein)

    return WeekOfDishes(
        dishes=dishes, rejected=tuple(rejected), unfilled=tuple(unfilled)
    )


async def _dish_names(
    *,
    ask,
    build_names_prompt,
    meal_type,
    slots,
    hint,
    cuisine_region,
    taken,
) -> list[str]:
    """One call for this meal type's names, in the order the slots want them."""
    wanted = len(slots) + NAME_SPARES
    prompt = build_names_prompt(
        meal_type=meal_type,
        target_calories=int(slots[0].target_calories),
        count=wanted,
        cuisine_region=cuisine_region,
        exclude_meal_names=sorted(taken) or None,
    )
    note = _slot_note_block(slots[0].note)
    try:
        answer = await ask(
            prompt + note + VARIETY_RULE + hint,
            DISH_NAME_SYSTEM.format(count=wanted),
        )
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
    used: set[str],
    price,
    cuisine_region,
    language,
    taken: set[str],
    eaten_today: tuple[str, ...] = (),
    proteins_today: frozenset[str] = frozenset(),
    rejected: list[RejectedDish],
    mint,
) -> SavedRecipeSnapshot | None:
    """The dish this slot keeps, after at most MAX_DISH_ATTEMPTS tries."""
    candidates = [name for name in names if dish_key(name) not in taken | used]
    if names and not candidates:
        # Every name offered is already on the table this week. Asking again
        # with no name invites the model to repeat itself a third time.
        rejected.append(RejectedDish(name=names[0], reasons=("ALREADY_IN_THE_WEEK",)))
        return None
    attempt, budget, bonus = 0, MAX_DISH_ATTEMPTS, PROTEIN_RETRY_BONUS
    while attempt < budget:
        name = candidates[attempt] if attempt < len(candidates) else None
        prompt = build_recipe_prompt(
            meal_name=name or "",
            meal_type=request.meal_type,
            target_calories=int(request.target_calories),
            cooking_time_minutes=request.minutes,
            cuisine_region=cuisine_region,
            protein_target=request.protein,
            carbs_target=request.carbs,
            fat_target=request.fat,
        )
        attempt += 1
        try:
            payload = await ask(
                prompt
                + _slot_note_block(request.note)
                + _day_so_far_block(eaten_today)
                + COOKING_FAT_RULE
                + ("" if name else NAME_IT_YOURSELF_RULE)
                + language_block(language),
                DISH_RECIPE_SYSTEM,
            )
        except Exception:  # noqa: BLE001 - one failed call is not the week
            continue
        if not isinstance(payload, dict):
            continue
        if name and not str(payload.get("name") or "").strip():
            payload["name"] = name
        snapshot = snapshot_from_ai_recipe(
            payload, dish_id=f"ai_{mint()}", language=language
        )
        if snapshot is None:
            rejected.append(RejectedDish(name=str(name or ""), reasons=("UNREADABLE",)))
            continue
        if price is not None:
            try:
                snapshot = priced_snapshot(
                    snapshot, await price(pricing_lines(snapshot))
                )
            except Exception:  # noqa: BLE001 - an unpriced dish is turned away
                rejected.append(
                    RejectedDish(name=snapshot.name, reasons=("NOT_PRICED",))
                )
                continue
        reasons = review_ai_dish(snapshot, taken=frozenset(taken))
        if (
            not reasons
            and attempt < len(candidates)
            and _repeats_today(snapshot, proteins_today)
        ):
            # Only while another name is left to try. A second helping of
            # chicken is a worse week; an empty slot is a broken one, so the
            # last name standing is taken whatever it is built on. The try
            # is given back once, so the extra name can actually be reached.
            budget += bonus
            bonus = 0
            rejected.append(
                RejectedDish(
                    name=snapshot.name,
                    reasons=(f"SAME_PROTEIN_TODAY_{main_protein(snapshot).upper()}",),
                )
            )
            continue
        if not reasons:
            if name:
                used.add(dish_key(name))
            return snapshot
        rejected.append(RejectedDish(name=snapshot.name, reasons=reasons))
    return None


def _repeats_today(
    snapshot: SavedRecipeSnapshot, proteins_today: frozenset[str]
) -> bool:
    """Whether this dish eats again what the day has already eaten."""
    protein = main_protein(snapshot)
    return bool(protein) and protein in proteins_today
