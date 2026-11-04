"""How a region eats, so the planner can serve a believable day.

The composer used to carry Viet Nam's rules as module constants: a 55 g rice
step from the National Institute of Nutrition, rice at lunch and dinner but
never at breakfast, and a list of Vietnamese cooking words that mark a dish as
one eaten with rice. Those are right for Viet Nam and silently wrong anywhere
else, with nothing to warn the reader.

They live here instead, one pack per region, resolved from the user's
timezone. Viet Nam stays the default, and its numbers are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MealLocale:
    """The serving rules of one region."""

    code: str
    # The staple served beside a dish, in the step the log endpoint accepts.
    staple_name: str
    staple_step_grams: int
    # Protein, carbohydrate and fat in one step.
    staple_step_macros: tuple[float, float, float]
    max_staple_steps: int
    # A dish that takes the staple is never served without at least this many.
    min_staple_steps: int
    # Slot indexes the staple belongs to: 0 breakfast, 1 lunch, 2 dinner.
    staple_slots: frozenset[int]
    # How a day's calories usually split across those slots.
    slot_calorie_shares: tuple[float, ...]
    # Cooked this way, a dish is eaten with the staple beside it.
    staple_dish_words: tuple[str, ...]
    # A dish named this way is already a whole meal and takes no staple.
    standalone_dish_words: tuple[str, ...]
    # Names a logged food item goes by when it is the staple itself, used to
    # read back from the diary whether a meal came with one.
    staple_item_words: tuple[str, ...]

    def is_staple_item(self, name: str) -> bool:
        """Whether a logged food item is the staple served beside a dish."""
        folded = " ".join(str(name or "").strip().casefold().split())
        if not folded:
            return False
        return any(word in folded for word in self.staple_item_words)

    def takes_staple(self, folded_name: str) -> bool:
        """Whether a dish of this name is eaten with the staple.

        ``folded_name`` is the dish name lowercased with its diacritics
        stripped, so the word lists can stay plain ASCII.
        """
        if any(word in folded_name for word in self.standalone_dish_words):
            return False
        return any(word in folded_name for word in self.staple_dish_words)


# Viet Nam NIN counts a cereal serving as 55 g steamed rice (20 g carbohydrate).
# Breakfast is pho, banh mi, xoi or chao, eaten on its own, so rice goes with
# lunch and dinner only.
VIETNAM = MealLocale(
    code="vn",
    staple_name="Cơm trắng",
    staple_step_grams=55,
    staple_step_macros=(1.5, 20.0, 0.15),
    max_staple_steps=4,
    min_staple_steps=1,
    staple_slots=frozenset({1, 2}),
    slot_calorie_shares=(0.25, 0.375, 0.375),
    staple_dish_words=(
        "kho",
        "ram",
        "rang",
        "xao",
        "chien",
        "nuong",
        "hap",
        "luoc",
    ),
    standalone_dish_words=("pho", "bun", "mi ", "chao", "com", "banh", "sup"),
    staple_item_words=("cơm", "com trang", "gạo", "steamed rice", "white rice"),
)

DEFAULT_LOCALE = VIETNAM

_LOCALES: dict[str, MealLocale] = {locale.code: locale for locale in (VIETNAM,)}

# Only zones a pack exists for. An unknown zone falls back to the default
# rather than guessing a region's eating habits from its offset.
_TIMEZONE_LOCALES = {
    "asia/ho_chi_minh": "vn",
    "asia/saigon": "vn",
    "asia/hanoi": "vn",
    "asia/phnom_penh": "vn",
    "asia/vientiane": "vn",
    "asia/bangkok": "vn",
}


def locale_by_code(code: str | None) -> MealLocale | None:
    """The pack with this code, or None when none is registered."""
    if not code:
        return None
    return _LOCALES.get(str(code).strip().casefold())


def locale_for_timezone(timezone: str | None) -> MealLocale:
    """The pack to plan with for a user in this timezone.

    A timezone is a weak proxy for where somebody eats - a Vietnamese user in
    California reads as American - so it is a starting point, not a verdict.
    Store a region on the profile when one is worth asking for.
    """
    key = str(timezone or "").strip().casefold()
    code = _TIMEZONE_LOCALES.get(key)
    return _LOCALES.get(code or "", DEFAULT_LOCALE)
