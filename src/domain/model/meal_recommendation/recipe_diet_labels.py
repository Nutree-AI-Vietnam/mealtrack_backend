"""The recipe browse diet rule: one source for filters and labels.

``GET /v1/recipes?diet=vegetarian|no-pork``, the catalog projection columns
behind that filter, and the ``is_vegetarian`` / ``is_pork_free`` labels all
use this rule. It looks for words in the recipe name, description and
ingredient names, before any translation.

Plan generation and swap validation use the stricter planner rule in
``weekly_plan_generation_service`` instead; this rule only labels and
filters browse lists.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

BROWSE_MEAT_WORDS: tuple[str, ...] = (
    "beef",
    "chicken",
    "pork",
    "fish",
    "shrimp",
    "meat",
    "thịt",
    "gà",
    "bò",
    "cá",
)
BROWSE_PORK_WORDS: tuple[str, ...] = (
    "pork",
    "ham",
    "bacon",
    "sausage",
    "thịt heo",
    "thịt lợn",
)


def recipe_browse_text(
    name: str, description: str | None, ingredient_names: Iterable[str]
) -> str:
    """Casefolded text the browse diet and dislike filters search."""
    return " ".join([name, description or "", *ingredient_names]).casefold()


@dataclass(frozen=True)
class RecipeDietLabels:
    is_vegetarian: bool
    is_pork_free: bool

    @classmethod
    def from_browse_text(cls, browse_text: str) -> RecipeDietLabels:
        return cls(
            is_vegetarian=not any(word in browse_text for word in BROWSE_MEAT_WORDS),
            is_pork_free=not any(word in browse_text for word in BROWSE_PORK_WORDS),
        )

    @classmethod
    def for_recipe(
        cls, name: str, description: str | None, ingredient_names: Iterable[str]
    ) -> RecipeDietLabels:
        return cls.from_browse_text(
            recipe_browse_text(name, description, ingredient_names)
        )

    def allows(self, diet: str | None) -> bool:
        """Whether a browse ``diet`` filter keeps this recipe; unknown diets match none."""
        if not diet or diet == "any":
            return True
        if diet == "vegetarian":
            return self.is_vegetarian
        if diet == "no-pork":
            return self.is_pork_free
        return False

    def to_dict(self) -> dict[str, bool]:
        return {"is_vegetarian": self.is_vegetarian, "is_pork_free": self.is_pork_free}

    @classmethod
    def from_dict(cls, data: dict | None) -> RecipeDietLabels | None:
        if not isinstance(data, dict):
            return None
        return cls(
            is_vegetarian=bool(data.get("is_vegetarian")),
            is_pork_free=bool(data.get("is_pork_free")),
        )
