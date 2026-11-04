"""What a hundred grams of food can physically be.

Macros reach us from three places that can all be wrong: our own
``food_reference`` table, an external food API, and an AI estimate. None of
them is verified, and the table fills itself from the other two, so a bad
number written once is served forever.

These bounds are physics, not nutrition policy. A hundred grams of anything
holds at most a hundred grams of protein, carbohydrate and fat together, and
pure fat — the most energy-dense food there is — carries 900 kcal. Nothing
edible beats either. That is why this file never needs tuning as the
catalogue grows: it does not describe what we know about food, only what food
cannot be.

Deliberately not checked here: a reading of zero. Salt, water, pepper and tea
really are zero, telling them apart from a bad lookup needs a word list, and
a missing ingredient understates a meal by its own calories while an
impossible one can overstate it twenty-fold.
"""

from src.domain.model.nutrition.macros import Macros

# A 100 g sample cannot hold more than 100 g of macronutrients. The slack
# absorbs rounding in sources that store one decimal place.
MAX_GRAMS_PER_100G = 102.0

# Pure fat is 900 kcal per 100 g. Nothing edible is denser.
MAX_KCAL_PER_100G = 900.0


def per_100g_problem(
    protein: float | None,
    carbs: float | None,
    fat: float | None,
    fiber: float | None = 0.0,
) -> str | None:
    """Say why this per-100g reading cannot be food, or None when it can.

    The reason is a short tag meant for logs and metrics: ``missing``,
    ``negative``, ``mass`` or ``energy``.
    """
    values = (protein, carbs, fat, fiber)
    if any(value is None for value in values):
        return "missing"
    protein, carbs, fat, fiber = (float(value) for value in values)  # type: ignore[arg-type]
    if any(value < 0 for value in (protein, carbs, fat, fiber)):
        return "negative"
    if protein + carbs + fat > MAX_GRAMS_PER_100G:
        return "mass"
    if Macros.raw_total_calories(protein, carbs, fat, fiber) > MAX_KCAL_PER_100G:
        return "energy"
    return None


def is_plausible_per_100g(
    protein: float | None,
    carbs: float | None,
    fat: float | None,
    fiber: float | None = 0.0,
) -> bool:
    """Whether a per-100g reading could describe real food."""
    return per_100g_problem(protein, carbs, fat, fiber) is None
