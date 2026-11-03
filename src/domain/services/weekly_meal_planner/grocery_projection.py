"""Derive grocery quantities from scaled recipe lines, overrides, and pantry.

Interaction flags never change the calculated quantity. Slot overrides are
applied to a copy of the lines and do not rewrite the catalog payload.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    estimate_servings_from_weight,
    is_nutrition_safe_ingredient,
    is_planner_eligible,
)

SYNTHETIC_INGREDIENT_ID_OFFSET = 1_000_000_000


def deterministic_ingredient_id(name: str) -> int:
    """Generate a stable positive 31-bit integer ID for an ingredient name, offset to avoid collision with database food_reference_id."""
    norm = unicodedata.normalize("NFC", (name or "").strip().lower())
    val = zlib.crc32(norm.encode("utf-8")) % 1_000_000_000
    return SYNTHETIC_INGREDIENT_ID_OFFSET + val


# Seasonings and staples a Vietnamese kitchen usually already has. Grocery
# lists fold them under their own group, and the weekly generator leaves them
# out when it counts how many fresh ingredients a week needs.
#
# Vietnamese names lead with the head noun ("muối hột", "tiêu xay"). Matching
# keeps accents: "dầu" (oil) is not "đậu" (beans).
_VI_HEADS = (
    "muối",
    "đường",
    "nước mắm",
    "tiêu",
    "hạt tiêu",
    "dầu",
    "hạt nêm",
    "bột nêm",
    "bột ngọt",
    "mì chính",
    "nước tương",
    "xì dầu",
    "giấm",
    "dấm",
    "tỏi",
    "hành tím",
    "hành khô",
    "hành phi",
    "ớt",
    "gạo",
    "bột năng",
    "bột bắp",
)
# Whole names only: "nước dừa" is coconut water, not tap water.
_VI_EXACT = frozenset(
    {"nước", "nước lọc", "nước sôi", "nước ấm", "nước lạnh", "cơm", "cơm trắng"}
)
# Look like a staple head but are fresh food bought for one dish.
_VI_EXCEPTIONS = ("ớt chuông", "ớt đà lạt", "tỏi tây", "gạo nếp", "dầu cá")

# English names end with the head noun ("sea salt", "brown sugar").
_EN_TAILS = (
    "salt",
    "sugar",
    "black pepper",
    "white pepper",
    "ground pepper",
    "peppercorns",
    "fish sauce",
    "soy sauce",
    "oyster sauce",
    "oil",
    "vinegar",
    "msg",
    "seasoning powder",
    "garlic",
    "shallot",
    "shallots",
    "cornstarch",
    "corn starch",
    "tapioca starch",
)
_EN_EXACT = frozenset(
    {
        "pepper",
        "rice",
        "white rice",
        "jasmine rice",
        "steamed rice",
        "chili",
        "chilli",
        "chilies",
        "chillies",
        "water",
        "hot water",
        "warm water",
        "cold water",
        "boiling water",
    }
)
_EN_EXCEPTIONS = ("fish oil",)

_PARENTHESES = re.compile(r"\([^)]*\)")
_SPACES = re.compile(r"\s+")
_FILLERS = ("to taste", "for garnish", "optional")


def is_pantry_staple(name: str) -> bool:
    """Return whether a recipe ingredient is a seasoning or kitchen staple."""
    folded = _staple_name(name)
    if not folded:
        return False
    if folded in _VI_EXACT or folded in _EN_EXACT:
        return True
    if _starts_with_any(folded, _VI_EXCEPTIONS) or _ends_with_any(
        folded, _EN_EXCEPTIONS
    ):
        return False
    return _starts_with_any(folded, _VI_HEADS) or _ends_with_any(folded, _EN_TAILS)


def _staple_name(name: str) -> str:
    value = unicodedata.normalize("NFC", name or "").casefold()
    value = _PARENTHESES.sub(" ", value).split(",", 1)[0]
    for filler in _FILLERS:
        value = value.replace(filler, " ")
    return _SPACES.sub(" ", value).strip(" .:-")


def _starts_with_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(value == phrase or value.startswith(f"{phrase} ") for phrase in phrases)


def _ends_with_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(value == phrase or value.endswith(f" {phrase}") for phrase in phrases)


# ---------------------------------------------------------------------------
# Canonical shopping identity
#
# Recipes come from many sources, so the same thing is written in several ways:
# "thịt heo" and "thịt lợn", "hành lá" and "hành ống", "lạc" and "đậu phộng".
# A grocery list that keeps them apart makes the user buy the same item twice
# and shows a different word on the weekly list than on the day list.
#
# canonical_ingredient() folds a recipe line's name onto a shopping identity:
# a stable key to merge on. It merges only names for the *same purchase*. Cuts
# a market sells separately stay separate: sườn (ribs) is not ba chỉ (belly),
# and tôm khô is not tôm. The name shown comes from the catalog's own wording
# (the most used name in the group), never from a word list, so an
# English-language recipe is never relabelled in Vietnamese.
#
# Nutrition never reads this. Merging only affects what the user buys.
# ---------------------------------------------------------------------------
_LEADING_AMOUNT = re.compile(r"^\d+[\d.,/]*\s*")

# Dropped wherever they appear: they describe the cooking, not the purchase.
_PREP_PHRASES = (
    "thái lát",
    "thái nhỏ",
    "thái sợi",
    "thái khúc",
    "cắt khúc",
    "cắt nhỏ",
    "cắt miếng",
    "băm nhỏ",
    "băm nhuyễn",
    "xắt lát",
    "xắt nhỏ",
    "đập dập",
    "rửa sạch",
    "bóc vỏ",
    "bỏ vỏ",
    "bỏ hạt",
    "để ráo",
    "tùy thích",
    "tùy khẩu vị",
    "vừa ăn",
    "vừa đủ",
    "các loại",
    "thái mỏng",
    "sliced",
    "chopped",
    "minced",
    "diced",
    "peeled",
    "to taste",
    "for garnish",
    "optional",
    "fresh",
    "finely",
)

# Region and dialect pairs for the same thing. The value is the word kept.
# Only unambiguous pairs: "bắp" alone can mean corn or calf muscle, so
# "bắp bò" is excluded below before this map runs.
_WORD_SYNONYMS = {
    "lợn": "heo",
    "ngô": "bắp",
    "lạc": "đậu phộng",
    "vừng": "mè",
}

# Whole-name groups, checked after word folding. The value is the shopping
# identity. The wording a merged line is shown in is not taken from here:
# _display_name keeps whichever name the recipes used most, so an English
# catalog is never relabelled in Vietnamese, or the other way round.
_NAME_GROUPS = {
    # Pork, beef, chicken written with or without the generic head "thịt".
    "heo": "thit_heo",
    "thịt heo": "thit_heo",
    "thịt heo nạc": "thit_heo_nac",
    "nạc heo": "thit_heo_nac",
    "thịt nạc heo": "thit_heo_nac",
    "heo xay": "thit_heo_xay",
    "thịt heo xay": "thit_heo_xay",
    "thịt xay": "thit_heo_xay",
    "ba chỉ": "ba_chi_heo",
    "thịt ba chỉ": "ba_chi_heo",
    "ba chỉ heo": "ba_chi_heo",
    "thịt ba chỉ heo": "ba_chi_heo",
    "bò": "thit_bo",
    "thịt bò": "thit_bo",
    "beef": "thit_bo",
    "gà": "thit_ga",
    "thịt gà": "thit_ga",
    "chicken": "thit_ga",
    "ức gà": "uc_ga",
    "thịt ức gà": "uc_ga",
    "chicken breast": "uc_ga",
    "đùi gà": "dui_ga",
    "pork": "thit_heo",
    # Alliums and herbs, the worst offenders for duplicate lines.
    "hành lá": "hanh_la",
    "hành ống": "hanh_la",
    "hành hoa": "hanh_la",
    "lá hành": "hanh_la",
    "green onion": "hanh_la",
    "green onions": "hanh_la",
    "spring onion": "hanh_la",
    "scallion": "hanh_la",
    "scallions": "hanh_la",
    "hành tím": "hanh_tim",
    "hành khô": "hanh_tim",
    "củ hành tím": "hanh_tim",
    "shallot": "hanh_tim",
    "shallots": "hanh_tim",
    "hành tây": "hanh_tay",
    "củ hành tây": "hanh_tay",
    "onion": "hanh_tay",
    "ngò": "ngo_ri",
    "ngò rí": "ngo_ri",
    "rau mùi": "ngo_ri",
    "mùi ta": "ngo_ri",
    "cilantro": "ngo_ri",
    "coriander": "ngo_ri",
    "ngò gai": "ngo_gai",
    "mùi tàu": "ngo_gai",
    "tỏi": "toi",
    "củ tỏi": "toi",
    "nhánh tỏi": "toi",
    "tép tỏi": "toi",
    "garlic": "toi",
    "gừng": "gung",
    "củ gừng": "gung",
    "ginger": "gung",
    "sả": "sa",
    "cây sả": "sa",
    "lemongrass": "sa",
    # Vegetables seen in the weekly list.
    "cà chua": "ca_chua",
    "tomato": "ca_chua",
    "tomatoes": "ca_chua",
    "củ cải": "cu_cai_trang",
    "củ cải trắng": "cu_cai_trang",
    "daikon": "cu_cai_trang",
    "cà rốt": "ca_rot",
    "carrot": "ca_rot",
    "carrots": "ca_rot",
    "giá": "gia_do",
    "giá đỗ": "gia_do",
    "giá đậu": "gia_do",
    "bean sprouts": "gia_do",
    "dưa chuột": "dua_leo",
    "dưa leo": "dua_leo",
    "cucumber": "dua_leo",
    "rau muống": "rau_muong",
    "water spinach": "rau_muong",
    "bắp cải": "bap_cai",
    "cabbage": "bap_cai",
    # Eggs, tofu and other staples bought by the piece.
    "trứng": "trung_ga",
    "trứng gà": "trung_ga",
    "egg": "trung_ga",
    "eggs": "trung_ga",
    "trứng vịt": "trung_vit",
    "đậu hũ": "dau_hu",
    "đậu phụ": "dau_hu",
    "tàu hũ": "dau_hu",
    "tofu": "dau_hu",
    "tôm": "tom",
    "tôm tươi": "tom",
    "shrimp": "tom",
    "prawn": "tom",
    "prawns": "tom",
}

# "bắp bò" is a beef shank, not corn: leave these names alone before the
# word map folds "ngô" onto "bắp".
_WORD_SYNONYM_BLOCKERS = ("bắp bò", "bắp chuối", "bắp cải")


@dataclass(frozen=True)
class CanonicalIngredient:
    """What a recipe line is bought as."""

    key: str
    # False when the name is unknown to the table and only normalization ran.
    is_known: bool = False


def normalize_ingredient_name(name: str) -> str:
    """Lower-case the name and drop amounts, notes and preparation words.

    Accents are kept: "cà" (eggplant, tomato) is not "cá" (fish).
    """
    value = unicodedata.normalize("NFC", str(name or "")).casefold()
    value = _PARENTHESES.sub(" ", value)
    value = value.replace("–", " ").replace("—", " ")
    value = _LEADING_AMOUNT.sub("", value.strip())
    for phrase in _PREP_PHRASES:
        value = value.replace(phrase, " ")
    value = _SPACES.sub(" ", value).strip(" .,:-")
    return value


def _fold_words(value: str) -> str:
    if any(blocker in value for blocker in _WORD_SYNONYM_BLOCKERS):
        return value
    words = [_WORD_SYNONYMS.get(word, word) for word in value.split(" ")]
    return " ".join(words)


def canonical_ingredient(name: str) -> CanonicalIngredient:
    """The shopping identity of a recipe line.

    An unknown name keeps its own identity, so nothing is merged by accident.
    """
    normalized = _fold_words(normalize_ingredient_name(name))
    if not normalized:
        return CanonicalIngredient(key="")
    group = _NAME_GROUPS.get(normalized)
    if group is not None:
        return CanonicalIngredient(key=group, is_known=True)
    return CanonicalIngredient(key=f"raw:{normalized}")


def fresh_ingredient_id(food_reference_id: int | None, name: str) -> int | None:
    """The grocery id a recipe line is bought under, or None for a staple."""
    if is_pantry_staple(name):
        return None
    if food_reference_id is not None:
        return int(food_reference_id)
    return deterministic_ingredient_id(name)


@dataclass(frozen=True)
class GroceryIngredient:
    position: int
    food_reference_id: int | None
    name: str
    quantity: Decimal | None
    unit: str | None
    category: str = "pantry"
    quantity_text: str | None = None
    quantity_confidence: str = "exact"

    def as_mapping(self) -> dict:
        return {
            "food_reference_id": self.food_reference_id,
            "quantity": self.quantity,
            "quantity_text": self.quantity_text,
        }


@dataclass(frozen=True)
class GrocerySlot:
    recipe_id: str | None
    publication_status: str
    nutrition_status: str
    is_active: bool
    base_servings: int | None
    serving_confidence: str
    people: int
    ingredients: tuple[GroceryIngredient, ...]
    recipe_override: Mapping | None = None
    day_index: int = 0


@dataclass(frozen=True)
class PantryAvailability:
    food_reference_id: int
    available_amount: Decimal | None
    available_unit: str | None = None


@dataclass(frozen=True)
class GroceryInteraction:
    food_reference_id: int
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False


@dataclass(frozen=True)
class DerivedGroceryItem:
    ingredient_id: int
    name: str
    category: str
    total_needed: float
    remaining: float
    unit: str
    available_amount: float | None
    status: str
    quantity_confidence: str
    contributions: tuple[tuple[int, float], ...]
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False
    daily_amounts: tuple[tuple[int, float, float], ...] = ()


def apply_recipe_override(
    ingredients: tuple[GroceryIngredient, ...], override: Mapping | None
) -> tuple[GroceryIngredient, ...]:
    if not override:
        return ingredients
    removed = {
        int(item["position"])
        for item in override.get("removed_ingredients") or []
        if isinstance(item, Mapping) and item.get("position") is not None
    }
    changes: dict[int, Mapping] = {}
    for change in override.get("ingredient_changes") or []:
        if not isinstance(change, Mapping) or change.get("position") is None:
            continue
        if change.get("new_quantity") is None:
            continue
        changes[int(change["position"])] = change
    updated: list[GroceryIngredient] = []
    for line in ingredients:
        if line.position in removed:
            continue
        change = changes.get(line.position)
        if change is None:
            updated.append(line)
            continue
        updated.append(
            replace(
                line,
                quantity=Decimal(str(change["new_quantity"])),
                unit=str(change.get("unit") or line.unit or ""),
            )
        )
    return tuple(updated)


def aggregate_grocery(
    slots: tuple[GrocerySlot, ...] | list[GrocerySlot],
    pantry: tuple[PantryAvailability, ...] | list[PantryAvailability],
    interactions: tuple[GroceryInteraction, ...] | list[GroceryInteraction] = (),
) -> tuple[DerivedGroceryItem, ...]:
    totals: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {
            "category": "pantry",
            "amount": Decimal("0"),
            "quantity_confidence": "verified",
            "contributions": [],
            "day_amounts": defaultdict(Decimal),
            "food_ids": set(),
            "names": Counter(),
        }
    )
    for slot in slots:
        if slot.recipe_id is None:
            continue
        if not is_planner_eligible(
            publication_status=slot.publication_status,
            nutrition_status=slot.nutrition_status,
            is_active=slot.is_active,
        ):
            continue
        multiplier, serving_confidence = _serving_multiplier(slot)
        for line in apply_recipe_override(slot.ingredients, slot.recipe_override):
            if not is_nutrition_safe_ingredient(line.as_mapping()):
                continue
            if line.quantity is None:
                continue
            normalized = normalize_ingredient_quantity(line.quantity, line.unit or "")
            ingredient_id = (
                int(line.food_reference_id)
                if line.food_reference_id is not None
                else deterministic_ingredient_id(line.name)
            )
            # One shopping identity per purchase, so "thịt heo" and "thịt lợn"
            # become one line with one name on every view.
            merge_key = canonical_ingredient(line.name).key or str(ingredient_id)
            key = (merge_key, normalized.dimension, normalized.unit)
            scaled = normalized.amount * multiplier
            bucket = totals[key]
            bucket["food_ids"].add(ingredient_id)
            bucket["names"][line.name.strip()] += 1
            bucket["category"] = line.category or "pantry"
            bucket["amount"] += scaled
            bucket["contributions"].append((line.position, float(scaled)))
            bucket["day_amounts"][slot.day_index] += scaled
            bucket["quantity_confidence"] = _confidence(
                current=bucket["quantity_confidence"],
                serving_confidence=serving_confidence,
                line_confidence=line.quantity_confidence,
            )

    pantry_by_food = {item.food_reference_id: item for item in pantry}
    interaction_by_food = {item.food_reference_id: item for item in interactions}
    items: list[DerivedGroceryItem] = []
    ordered = sorted(
        totals.items(),
        key=lambda item: (_representative_id(item[1]["food_ids"]), item[0][1:]),
    )
    for (_merge_key, dimension, unit), value in ordered:
        food_ids = value["food_ids"]
        ingredient_id = _representative_id(food_ids)
        # Stock and the user's own ticks were saved per food id, so a merged
        # line reads every id behind it.
        available = _merged_available(
            (pantry_by_food.get(food_id) for food_id in sorted(food_ids)),
            dimension=dimension,
            unit=unit,
        )
        status, remaining = grocery_status(value["amount"], available)
        flags = _merged_flags(ingredient_id, food_ids, interaction_by_food)
        stock_remaining = max(available or Decimal("0"), Decimal("0"))
        daily_amounts = []
        for day_index, day_needed in sorted(value["day_amounts"].items()):
            used_for_day = min(day_needed, stock_remaining)
            stock_remaining -= used_for_day
            daily_amounts.append(
                (
                    day_index,
                    float(day_needed),
                    float(day_needed - used_for_day),
                )
            )
        items.append(
            DerivedGroceryItem(
                ingredient_id=ingredient_id,
                name=_display_name(value["names"]),
                category=value["category"],
                total_needed=float(value["amount"]),
                remaining=float(remaining),
                unit=unit,
                available_amount=None if available is None else float(available),
                status=status,
                quantity_confidence=value["quantity_confidence"],
                contributions=tuple(sorted(value["contributions"])),
                checked=flags.checked,
                do_not_buy=flags.do_not_buy,
                manually_owned=flags.manually_owned,
                daily_amounts=tuple(daily_amounts),
            )
        )
    return tuple(items)


def grocery_status(required: Decimal, available: Decimal | None) -> tuple[str, Decimal]:
    if available is None or available <= 0:
        return "needed", required
    remaining = required - available
    if remaining <= 0:
        return "owned", Decimal("0")
    return "need_more", remaining


def _weighed_grams(slot: GrocerySlot) -> float:
    """Grams of the recipe that are actually weighed, liquids excluded."""
    total = Decimal("0")
    for line in slot.ingredients:
        if line.quantity is None:
            continue
        normalized = normalize_ingredient_quantity(line.quantity, line.unit or "")
        if normalized.dimension == "weight":
            total += normalized.amount
    return float(total)


def _serving_multiplier(slot: GrocerySlot) -> tuple[Decimal, str]:
    """How much of the recipe to buy, and how sure we are of it.

    A recipe that states its servings is trusted. One that does not is
    estimated from its weighed ingredients, because the alternative -
    scaling by 1 - buys a whole family dish for one person.
    """
    if (
        slot.base_servings
        and slot.base_servings > 0
        and slot.serving_confidence != "unknown"
    ):
        return (
            Decimal(slot.people) / Decimal(slot.base_servings),
            slot.serving_confidence,
        )
    estimated = estimate_servings_from_weight(_weighed_grams(slot))
    if estimated is not None:
        return Decimal(slot.people) / Decimal(estimated), "estimated"
    return Decimal("1"), "unknown"


def _confidence(*, current: str, serving_confidence: str, line_confidence: str) -> str:
    if serving_confidence == "unknown":
        return "unscaled"
    if line_confidence != "exact":
        return "unconverted"
    if serving_confidence == "estimated":
        return "estimated"
    return current


def _display_name(names) -> str:
    """The one name a merged line shows, in the catalog's own wording.

    The most used name wins, then the shortest, then alphabetical order, so
    every view of the week names the ingredient the same way.
    """
    if not names:
        return ""
    return min(names.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]


def _representative_id(food_ids) -> int:
    """The id a merged line is reported under.

    A real food_reference id wins over a name-derived one, so the app keeps
    talking to the catalog about the same food.
    """
    real = [food_id for food_id in food_ids if food_id < SYNTHETIC_INGREDIENT_ID_OFFSET]
    return min(real) if real else min(food_ids)


def _merged_available(stocks, *, dimension: str, unit: str) -> Decimal | None:
    """Pantry stock held under any of a merged line's food ids."""
    total: Decimal | None = None
    for stock in stocks:
        amount = _available_amount(stock, dimension=dimension, unit=unit)
        if amount is None:
            continue
        total = amount if total is None else total + amount
    return total


def _merged_flags(
    ingredient_id: int, food_ids, interaction_by_food
) -> GroceryInteraction:
    """A tick on any merged food id applies to the whole line."""
    checked = False
    do_not_buy = False
    manually_owned = False
    for food_id in food_ids:
        flags = interaction_by_food.get(food_id)
        if flags is None:
            continue
        checked = checked or flags.checked
        do_not_buy = do_not_buy or flags.do_not_buy
        manually_owned = manually_owned or flags.manually_owned
    return GroceryInteraction(
        food_reference_id=ingredient_id,
        checked=checked,
        do_not_buy=do_not_buy,
        manually_owned=manually_owned,
    )


def _available_amount(
    stock: PantryAvailability | None, *, dimension: str, unit: str
) -> Decimal | None:
    if stock is None or stock.available_amount is None:
        return None
    normalized = normalize_ingredient_quantity(
        stock.available_amount, stock.available_unit or unit
    )
    if normalized.dimension != dimension or normalized.unit != unit:
        return Decimal("0")
    return normalized.amount
