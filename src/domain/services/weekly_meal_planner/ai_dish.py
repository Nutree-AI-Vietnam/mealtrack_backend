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
from collections.abc import Mapping
from dataclasses import dataclass, replace

from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.plausibility import per_100g_problem
from src.domain.model.weekly_meal_planner.saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
    SavedRecipeStep,
)
from src.domain.model.weekly_meal_planner.weekly_meal_plan import SLOT_MEAL_TYPES
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
# How far a dish may land from what its own slot asked for. The absolute
# bounds above say "this is food"; they say nothing about whether it is
# this meal. A real week priced 1,580 kcal into a slot asking for 675 and
# the dish passed, because 1,580 is under MAX_DISH_KCAL — that day came to
# 3,320 against an 1,800 target, which is useless to somebody counting.
#
# The band is wide because a dish is not a portion and the pricing tiers
# disagree with each other. Measured over eighteen priced dishes, most
# landed between 100% and 140% of target; the band keeps those and turns
# away the 167%, 178% and 234% outliers that wrecked the day totals.
MIN_TARGET_SHARE = 0.6
MAX_TARGET_SHARE = 1.5
# Reasons worth another name but never worth an empty slot.
SOFT_REASONS = ("OFF_TARGET_CALORIES", "SAME_PROTEIN_TODAY")


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
    target_calories: int | None = None,
) -> tuple[str, ...]:
    """Reasons this dish cannot take a slot, or nothing when it can.

    ``taken`` holds the keys of dishes the week already serves, so a model
    that repeats itself does not fill the week with one meal — the complaint
    that sent the planner here in the first place.

    ``target_calories`` is what this slot asked for. Without it the dish is
    only checked for being food at all; with it, a dish that is food but
    twice the meal is turned away too.
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
    elif target_calories and target_calories > 0:
        share = kcal / float(target_calories)
        if not MIN_TARGET_SHARE <= share <= MAX_TARGET_SHARE:
            reasons.append(f"OFF_TARGET_CALORIES_{round(share * 100)}PCT")

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


class PricingUnavailable(RuntimeError):
    """Every tier failed, so this dish's macros are not knowable right now.

    Distinct from a dish being wrong: nothing the model wrote is at fault,
    and retrying with a different dish will fail the same way.
    """


def priced_snapshot(snapshot: SavedRecipeSnapshot, priced) -> SavedRecipeSnapshot:
    """The dish with the macros the backend worked out for its ingredients.

    A generated recipe carries grams and nothing else — the recipe prompt
    asks for no macros on purpose, because the backend prices every
    ingredient through food_reference, FatSecret and, failing both, an
    estimate. Without this step a dish sums to nothing and is turned away
    for having no calories, which is how a week came back empty.

    ``priced`` is what NutritionLookupService.calculate_meal_macros returns.

    Raises PricingUnavailable when it comes back with no energy at all for
    a dish that has ingredients with weight. Every tier failing returns
    zeros rather than raising, and those zeros used to be written straight
    onto the snapshot: the dish then summed to nothing, was turned away for
    implausible calories, and a whole week died blaming the dishes for an
    outage in our own pricing. A caller that cannot price is told so.
    """
    by_name = {
        str(line.name).strip().casefold(): line
        for line in getattr(priced, "ingredients", []) or []
    }
    lines = tuple(
        _with_macros(line, by_name.get(str(line.name).strip().casefold()))
        for line in snapshot.ingredients
    )
    macros = (
        float(getattr(priced, "protein", 0.0) or 0.0),
        float(getattr(priced, "carbs", 0.0) or 0.0),
        float(getattr(priced, "fat", 0.0) or 0.0),
    )
    weighed = any(line.grams for line in snapshot.ingredients)
    if weighed and not any(value > 0 for value in macros):
        raise PricingUnavailable(snapshot.name)
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
# How much of the catalogue to show. Eight was right for a style hint and
# far too few for a knowledge base: a meal type needs seven distinct dishes
# out of it, and the rows it draws on include headlines that yield nothing.
# Forty rows cost about 500 tokens on a call that already runs to 1,500.
MAX_REFERENCE_DISHES = 40


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


# The same families as PROTEIN_FAMILIES, as a dish name says them. A dish
# off the region's list can be placed before it is cooked, which is the
# difference between picking a second protein and paying a model call to
# find out you picked one.
#
# These keep their diacritics, and must. dish_key strips them so that "Cá
# Kho Tộ" and "ca kho to" are one dish, but stripped of its accent "cà
# chua" — a tomato — reads as "cá", and "Đậu hũ sốt cà chua" was placed as
# a fish dish. A tone mark is the whole word here.
PROTEIN_WORDS = {
    "gà": "chicken",
    "chicken": "chicken",
    "bò": "beef",
    "beef": "beef",
    "heo": "pork",
    "lợn": "pork",
    "sườn": "pork",
    "thịt": "pork",
    "nem": "pork",
    "pork": "pork",
    "cá": "fish",
    "fish": "fish",
    "tôm": "shrimp",
    "shrimp": "shrimp",
    "mực": "squid",
    "cua": "crab",
    "trứng": "egg",
    "egg": "egg",
    "hũ": "tofu",
    "phụ": "tofu",
    "tofu": "tofu",
}


def protein_of_name(name: str) -> str:
    """What a dish of this name is built on, or "" when the name is silent.

    Only safe on a curated list: "Cơm gà xối mỡ" is chicken, but so is
    "Canh gà cốt dừa kiểu Thái Lan", and a crawled headline could say
    anything. The region's own list is written to be read this way.
    """
    words = re.split(r"[^\w]+", str(name or "").casefold(), flags=re.UNICODE)
    return next((PROTEIN_WORDS[word] for word in words if word in PROTEIN_WORDS), "")


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
    """The catalogue as what this kitchen knows, which is what it is.

    This block used to end "Write your own dish. Do not reuse these names."
    Eight names were shown and then forbidden, and the week that came back
    was "Phở cá hồi" and "Cơm cà tím cà ri đậu gà" — a model doing exactly
    as it was told, with nothing real left to reach for.

    Measured over three wordings against the same catalogue sample: the old
    one put 0 of 21 real dishes on the table, this one 19, and a third that
    allowed nothing but the list pasted a blog headline into a slot. This
    one also reads a dish back out of a headline — "Gỏi cuốn tôm thịt" out
    of "Gỏi cuốn tôm thịt chuẩn vị miền Tây ai cũng mê" — which is the only
    way a crawled table is usable at all.
    """
    usable = [name.strip() for name in names if name and name.strip()]
    if not usable:
        return ""
    listed = "\n".join(f"- {name}" for name in usable[:MAX_REFERENCE_DISHES])
    return (
        "\nThis kitchen's recipe catalogue, as the rows were entered — some"
        f" are dish names, some are blog headlines:\n{listed}"
        "\nUse it as knowledge of what this kitchen cooks. Prefer a dish"
        " that is in it. Where a row is a headline rather than a dish, take"
        " the dish out of it and use that name, not the headline."
        "\nSkip any row that is not a meal somebody sits down to eat: a"
        " drink, a dessert, a snack, baby food, a seasoning, or an article"
        " about losing weight. The catalogue holds those too."
        "\nYou may name a dish that is not in the list, but only one a home"
        " cook here would recognise by that name."
        "\nNever invent a dish by combining one dish's base with another's"
        " protein."
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


def _catalogue_by_meal(
    reference: Mapping[str, tuple[str, ...]] | tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """The catalogue keyed by meal type, whichever shape the caller sent.

    A flat tuple is the older shape and every meal type sees all of it.
    """
    if isinstance(reference, Mapping):
        return {str(key): tuple(value) for key, value in reference.items()}
    rows = tuple(reference)
    return dict.fromkeys(SLOT_MEAL_TYPES, rows) if rows else {}


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

# What review_recipe_safety reads the steps for, said plainly. Seven of nine
# dishes lost in a week were real Vietnamese dishes the gate was right to
# stop — phở whose steps never said the chicken was cooked, thịt luộc cuốn
# with no oil named, bò tái. The gate is not the problem: nothing had told
# the model what the steps have to say, so it wrote a recipe a cook would
# follow fine and a reviewer could not clear.
KITCHEN_RULES = (
    "\nThe steps are checked before this dish is used. They must say:"
    "\n- that every meat, fish, seafood or egg is cooked through — use"
    ' "chín", "chín kỹ" or a cooking word like luộc, hấp, kho, rang, chiên,'
    " xào or nướng in the step that cooks it."
    "\n- the cooking oil or fat, listed as an ingredient in grams, whenever"
    " the dish is fried, stir-fried, deep-fried or sauteed."
    "\n- boiling, for fresh bamboo shoots, cassava or kidney beans."
    "\nNothing may be served rare or raw: no tái, no sống, no sashimi. Write"
    " the fully cooked version of the dish instead."
)


async def generate_week_of_dishes(
    requests: tuple[DishRequest, ...],
    *,
    ask,
    build_names_prompt,
    build_recipe_prompt,
    price=None,
    reference_dishes: Mapping[str, tuple[str, ...]] | tuple[str, ...] = (),
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
    memory = _memory_block(memory_text)
    catalogue = _catalogue_by_meal(reference_dishes)

    by_meal: dict[str, list[DishRequest]] = {}
    for request in requests:
        by_meal.setdefault(request.meal_type, []).append(request)

    # Per day, the protein the names already picked. Filled from the names
    # themselves rather than the cooked dish, so the next meal type can step
    # around it before a single call is spent.
    planned: dict[int, str] = {}

    for meal_type, slots in by_meal.items():
        names = await _dish_names(
            avoid=tuple(planned.get(slot.day_index, "") for slot in slots),
            ask=ask,
            build_names_prompt=build_names_prompt,
            meal_type=meal_type,
            slots=slots,
            hint=reference_block(catalogue.get(meal_type, ())) + memory,
            cuisine_region=cuisine_region,
            taken=taken | offered,
        )
        offered.update(dish_key(name) for name in names)
        for slot, name in zip(slots, names, strict=False):
            protein = protein_of_name(name)
            if protein:
                planned[slot.day_index] = protein
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
    avoid: tuple[str, ...] = (),
) -> list[str]:
    """This meal type's names: picked from the region's list, or asked for.

    A region with a list costs no call at all, which is three fewer a week.
    """
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
    return _arrange_by_day(_names_of(answer), avoid)


def _arrange_by_day(names: list[str], avoid: tuple[str, ...]) -> list[str]:
    """Order the names so a day's second meal is not its first's protein.

    The model writes a meal type's names in one answer, knowing nothing of
    the day each will land on. Reordering costs nothing and spares the
    recipe call that would otherwise cook a second helping of chicken and
    throw it away. Names it cannot place keep their order.
    """
    if not avoid or not names:
        return names
    pool, picked = list(names), []
    for index in range(len(names)):
        unwanted = avoid[index] if index < len(avoid) else ""
        choice = next(
            (name for name in pool if protein_of_name(name) != unwanted), pool[0]
        )
        picked.append(choice)
        pool.remove(choice)
    return picked


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
                + KITCHEN_RULES
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
            except PricingUnavailable:
                # Not this dish's fault and not fixable by writing another:
                # every tier is down, and the next twenty slots would each
                # pay for a recipe only to be turned away the same way.
                raise
            except Exception:  # noqa: BLE001 - an unpriced dish is turned away
                rejected.append(
                    RejectedDish(name=snapshot.name, reasons=("NOT_PRICED",))
                )
                continue
        reasons = review_ai_dish(
            snapshot,
            taken=frozenset(taken),
            target_calories=request.target_calories,
        )
        # Two kinds of no. A hard one — unsafe, not food, already on the
        # table — means this dish never goes in a slot. A soft one — off
        # its slot's target, or the day's protein a second time — means a
        # better dish exists, and is worth one more name. Neither is worth
        # an empty slot, so the last name standing is taken as it is.
        hard = tuple(r for r in reasons if not r.startswith(SOFT_REASONS))
        if hard:
            rejected.append(RejectedDish(name=snapshot.name, reasons=reasons))
            continue
        soft = reasons or (
            (f"SAME_PROTEIN_TODAY_{main_protein(snapshot).upper()}",)
            if _repeats_today(snapshot, proteins_today)
            else ()
        )
        if soft and attempt < len(candidates):
            budget += bonus
            bonus = 0
            rejected.append(RejectedDish(name=snapshot.name, reasons=soft))
            continue
        if name:
            used.add(dish_key(name))
        return snapshot
    return None


def _repeats_today(
    snapshot: SavedRecipeSnapshot, proteins_today: frozenset[str]
) -> bool:
    """Whether this dish eats again what the day has already eaten."""
    protein = main_protein(snapshot)
    return bool(protein) and protein in proteins_today
