"""What the planner remembers about how one person eats.

Built from the meals the user actually logged, which is a far stronger
signal than anything they ever typed into a preferences screen: adding a
recipe once says little, eating it twelve times says a lot.

This is computed when a week is planned rather than stored. It is one
aggregate query over the log, a plan is generated about once a week, and a
stored copy would only add a table, a rebuild job and a staleness bug. Give
it a table when something reads it often enough to notice the cost.

No vector store either. Retrieval here is always "the row for this user", and
a single person's habits are a handful of facts - there is no corpus to
search. A vector index would earn its keep for finding dishes inside a large
catalog, which is a different question.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

# Under this many logged meals the shape numbers are noise, so the planner is
# told not to lean on them.
CONFIDENT_MEAL_COUNT = 12
# How much of an ingredient's affinity weight counts as "eats this often".
LIKED_WEIGHT_FLOOR = 0.15
# Dishes kept as "recently eaten", so a new week can avoid repeating them.
RECENT_DISH_LIMIT = 12
# Skips before a dish is treated as refused rather than missed. One skip is
# usually a busy evening, not a verdict on the food; twice is a pattern.
SKIP_REFUSAL_COUNT = 2


@dataclass(frozen=True)
class LoggedMealShape:
    """One logged meal, reduced to what says how this person eats."""

    dish_name: str
    meal_type: str | None = None
    cuisine: str | None = None
    # Names of the food items logged on the meal. The count stands in for how
    # many dishes were eaten, and the names say whether a staple was among
    # them - which is a regional question, so the locale answers it.
    item_names: tuple[str, ...] = ()

    @property
    def item_count(self) -> int:
        return len(self.item_names)


@dataclass(frozen=True)
class UserFoodMemory:
    """How this person eats, as far as their log shows."""

    # food_reference_id -> affinity weight in [0, 1].
    liked_ingredients: Mapping[int, float] = field(default_factory=dict)
    # Share of logged meals that came with a staple beside the dish.
    staple_share: float = 0.0
    # Average number of food items in a logged meal.
    items_per_meal: float = 0.0
    # Cuisines eaten, most eaten first.
    cuisines: tuple[str, ...] = ()
    # Dish names eaten recently, newest first.
    recent_dishes: tuple[str, ...] = ()
    meals_observed: int = 0
    # catalog recipe id -> how often the user skipped it when it was planned.
    skipped_dishes: Mapping[str, int] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        """Whether there is enough log to plan differently because of it."""
        return self.meals_observed >= CONFIDENT_MEAL_COUNT

    @property
    def liked_ingredient_ids(self) -> frozenset[int]:
        """Ingredients eaten often enough to be worth steering toward."""
        return frozenset(
            food_id
            for food_id, weight in self.liked_ingredients.items()
            if weight >= LIKED_WEIGHT_FLOOR
        )

    @property
    def refused_recipe_ids(self) -> frozenset[str]:
        """Dishes skipped often enough to stop planning them."""
        return frozenset(
            recipe_id
            for recipe_id, count in self.skipped_dishes.items()
            if count >= SKIP_REFUSAL_COUNT
        )

    def skip_score(self, recipe_id: str | None) -> float:
        """How reluctant to plan this dish again, in [0, 1].

        A skip says a lot about the dish and nothing reliable about its
        ingredients - the user may simply have eaten out - so it is read
        against the dish alone and never spread to what it is made of.
        """
        if not recipe_id:
            return 0.0
        count = self.skipped_dishes.get(recipe_id, 0)
        if count <= 0:
            return 0.0
        return min(count / SKIP_REFUSAL_COUNT, 1.0)

    def taste_score(self, ingredient_ids: Iterable[int]) -> float:
        """How much of a dish this person already eats, in [0, 1].

        Zero for an empty dish and for a user with no history, so a cold
        start changes nothing about how the week is planned.
        """
        ids = [int(value) for value in ingredient_ids]
        if not ids or not self.liked_ingredients:
            return 0.0
        total = sum(
            min(self.liked_ingredients.get(food_id, 0.0), 1.0) for food_id in ids
        )
        return max(0.0, min(total / len(ids), 1.0))

    def prompt_text(self) -> str:
        """The memory as lines for a generation prompt.

        Written for a reader, not parsed by anything, and empty when there is
        nothing worth saying - an empty block beats an invented one.
        """
        if self.meals_observed == 0 and not self.skipped_dishes:
            return ""
        lines = [f"Meals logged: {self.meals_observed}"]
        if self.cuisines:
            lines.append(f"Cuisines eaten: {', '.join(self.cuisines)}")
        if self.is_confident:
            lines.append(
                f"Eats a staple with {round(self.staple_share * 100)}% of meals"
            )
            lines.append(f"About {self.items_per_meal:.1f} items in a meal")
        if self.recent_dishes:
            lines.append(f"Ate recently: {', '.join(self.recent_dishes)}")
        refused = len(self.refused_recipe_ids)
        if refused:
            lines.append(f"Skipped {refused} planned dishes more than once")
        return "\n".join(lines)


EMPTY_MEMORY = UserFoodMemory()


def build_user_food_memory(
    *,
    affinity_weights: Mapping[int, float] | None = None,
    meals: Iterable[LoggedMealShape] = (),
    is_staple_item: Callable[[str], bool] | None = None,
    skipped_recipe_ids: Iterable[str] = (),
) -> UserFoodMemory:
    """Reduce the log, and what the user refused, to one memory.

    ``meals`` is newest first. ``skipped_recipe_ids`` repeats a recipe once
    per skip, so the counts fall out of it.
    """
    observed = list(meals)
    skips: dict[str, int] = {}
    for recipe_id in skipped_recipe_ids:
        key = str(recipe_id or "").strip()
        if key:
            skips[key] = skips.get(key, 0) + 1
    if not observed and not affinity_weights and not skips:
        return EMPTY_MEMORY

    cuisine_counts: dict[str, int] = {}
    recent: list[str] = []
    staple_meals = 0
    items = 0
    for meal in observed:
        if meal.cuisine:
            key = meal.cuisine.strip()
            if key:
                cuisine_counts[key] = cuisine_counts.get(key, 0) + 1
        name = (meal.dish_name or "").strip()
        if name and name not in recent and len(recent) < RECENT_DISH_LIMIT:
            recent.append(name)
        if is_staple_item is not None and any(
            is_staple_item(name) for name in meal.item_names
        ):
            staple_meals += 1
        items += meal.item_count

    count = len(observed)
    return UserFoodMemory(
        liked_ingredients=dict(affinity_weights or {}),
        staple_share=(staple_meals / count) if count else 0.0,
        items_per_meal=(items / count) if count else 0.0,
        cuisines=tuple(
            name
            for name, _ in sorted(
                cuisine_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ),
        recent_dishes=tuple(recent),
        meals_observed=count,
        skipped_dishes=skips,
    )
