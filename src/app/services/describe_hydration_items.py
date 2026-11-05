"""Turn the model's drink list into entries priced by Nutree's own formulas.

The model only reads the sentence. Listed drinks take the catalog serving and
macros a tapped entry of the same size gets; unlisted drinks take the model's
per-100 ml estimate after it is clamped here, or plain-drink values when the
model gave none. A drink the person named is never dropped for being outside
the catalog; only an item with no name is.
"""

import re
from dataclasses import dataclass
from typing import Any

from src.app.services.describe_log_parser import (
    DESCRIBE_MAX_ITEMS,
    clamp,
    finite,
    short_name,
)
from src.domain.model.hydration import Drink
from src.domain.model.nutrition.macros import Macros
from src.domain.services.hydration_catalog_service import (
    catalog_drink_id,
    find_by_id,
)

# Stored for drinks outside the catalog. The catalog deliberately has no
# "other" entry, so every feed titles these rows with the person's words.
UNLISTED_DRINK_ID = "other"
# Label-scanned drinks have their own catalog row; Ghi nhanh never uses it.
SCANNED_DRINK_ID = "scanned"
# Used when neither read of the sentence gave an unlisted drink values: water
# credit like tea or juice, and no macros the person did not say.
UNLISTED_DEFAULT_HYDRATION_WEIGHT = 0.9
UNLISTED_DEFAULT_ML = 250
SERVINGS_RANGE = (0.25, 10.0)
VOLUME_RANGE_ML = (1.0, 2000.0)
MACRO_MAX_G_PER_100ML = 100.0
KCAL_MAX_PER_100ML = 350.0
_ID_SEPARATORS = re.compile(r"[\s_]+")


@dataclass(frozen=True)
class DescribedDrink:
    name: str  # catalog name for listed drinks, the person's words otherwise
    volume_ml: int
    hydration_weight: float
    drink: Drink | None = None  # None when the drink is not in the catalog
    per_100ml: Macros | None = None  # the clamped estimate, unlisted drinks only
    estimate_defaulted: bool = False  # unlisted, and no values came from the model

    @property
    def drink_id(self) -> str:
        return self.drink.id if self.drink else UNLISTED_DRINK_ID

    @property
    def credited_ml(self) -> int:
        return int(self.volume_ml * self.hydration_weight + 0.5)

    def unlisted_macros(self) -> Macros:
        per_100ml = self.per_100ml or Macros(protein=0.0, carbs=0.0, fat=0.0)
        factor = self.volume_ml / 100.0
        return Macros(
            protein=round(per_100ml.protein * factor, 1),
            carbs=round(per_100ml.carbs * factor, 1),
            fat=round(per_100ml.fat * factor, 1),
            fiber=round(per_100ml.fiber * factor, 1),
            sugar=round(per_100ml.sugar * factor, 1),
        )


@dataclass(frozen=True)
class DescribedDrinks:
    drinks: list[DescribedDrink]
    dropped: int
    overflow: int

    @property
    def defaulted(self) -> int:
        return sum(1 for drink in self.drinks if drink.estimate_defaulted)


def normalize_drinks(items: list[dict[str, Any]]) -> DescribedDrinks:
    """Valid drinks in sentence order, at most eight."""
    valid = [drink for drink in map(_drink, items) if drink is not None]
    return DescribedDrinks(
        drinks=valid[:DESCRIBE_MAX_ITEMS],
        dropped=len(items) - len(valid),
        overflow=max(0, len(valid) - DESCRIBE_MAX_ITEMS),
    )


def catalog_drink(item: dict[str, Any]) -> Drink | None:
    """The catalog drink the item names, forgiving case and spacing in the id
    ("Milk Tea") and a catalog name in any app language ("Trà sữa")."""
    raw = str(item.get("catalog_id") or "").strip()
    candidate = _ID_SEPARATORS.sub("-", raw.lower())
    for drink_id in (
        candidate,
        catalog_drink_id(raw),
        catalog_drink_id(item.get("name")),
    ):
        if drink_id and drink_id != SCANNED_DRINK_ID:
            drink = find_by_id(drink_id)
            if drink is not None:
                return drink
    return None


def needs_estimate(item: dict[str, Any]) -> bool:
    """An unlisted drink the person named but the model gave no water share."""
    estimate = item.get("unlisted") or {}
    return (
        catalog_drink(item) is None
        and bool(short_name(item.get("name")))
        and finite(estimate.get("hydration_weight")) is None
    )


def _drink(item: dict[str, Any]) -> DescribedDrink | None:
    drink = catalog_drink(item)
    per_serving = finite(item.get("volume_ml_per_serving"))
    if per_serving is None or per_serving <= 0:
        per_serving = drink.default_ml if drink else UNLISTED_DEFAULT_ML
    servings = finite(item.get("servings"))
    servings = 1.0 if servings is None else clamp(servings, *SERVINGS_RANGE)
    volume_ml = int(clamp(per_serving * servings, *VOLUME_RANGE_ML) + 0.5)

    if drink is not None:
        return DescribedDrink(
            name=drink.name,
            volume_ml=volume_ml,
            hydration_weight=drink.hydration_weight,
            drink=drink,
        )

    name = short_name(item.get("name"))
    if not name:
        return None
    estimate = item.get("unlisted") or {}
    weight = finite(estimate.get("hydration_weight"))
    return DescribedDrink(
        name=name,
        volume_ml=volume_ml,
        hydration_weight=(
            UNLISTED_DEFAULT_HYDRATION_WEIGHT
            if weight is None
            else clamp(weight, 0.0, 1.0)
        ),
        per_100ml=_clamped_per_100ml(estimate),
        estimate_defaulted=weight is None,
    )


def _clamped_per_100ml(estimate: dict[str, Any]) -> Macros:
    protein, carbs, fat, sugar, fiber = (
        clamp(finite(estimate.get(key)) or 0.0, 0.0, MACRO_MAX_G_PER_100ML)
        for key in ("protein_g", "carbs_g", "fat_g", "sugar_g", "fiber_g")
    )
    total = protein + carbs + fat
    if total > MACRO_MAX_G_PER_100ML:
        scale = MACRO_MAX_G_PER_100ML / total
        protein, carbs, fat = protein * scale, carbs * scale, fat * scale
    sugar, fiber = min(sugar, carbs), min(fiber, carbs)
    kcal = Macros.raw_total_calories(protein, carbs, fat, fiber)
    scale = min(1.0, KCAL_MAX_PER_100ML / kcal) if kcal > 0 else 1.0
    return Macros(
        protein=protein * scale,
        carbs=carbs * scale,
        fat=fat * scale,
        fiber=fiber * scale,
        sugar=sugar * scale,
    )
