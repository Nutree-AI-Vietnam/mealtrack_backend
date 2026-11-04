"""The reply of POST /v1/log/describe and the ids its undo deletes.

Each saved item carries its kind's own payload (the shapes the water,
activity and supplement routes already return), so the app can apply it
with the helpers it has. The ids go to the replay record, never the reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.app.handlers.command_handlers.describe_hydration_command_handler import (
    SavedDrink,
    described_drink_payload,
)
from src.app.handlers.command_handlers.log_movement_command_handler import (
    _movement_response,
)
from src.app.handlers.command_handlers.supplement_command_handlers import SavedDose
from src.app.services.describe_any_items import ACTIVITY, DRINK, FOOD, SUPPLEMENT
from src.app.services.supplement_payloads import (
    intake_payload,
    list_entry_payload,
    product_payload,
)
from src.domain.model.meal import Meal
from src.domain.model.movement import MovementEntry
from src.domain.model.supplement import SupplementIntake, SupplementProduct
from src.domain.services.movement_catalog_service import localized_activity_name


@dataclass
class SavedSentence:
    """Everything one sentence saved, in one transaction."""

    day: date
    meal: Meal | None = None
    meal_day: date | None = None
    drinks: list[SavedDrink] = field(default_factory=list)
    activities: list[MovementEntry] = field(default_factory=list)
    doses: list[tuple[SavedDose, SupplementProduct, list[SupplementIntake]]] = field(
        default_factory=list
    )

    @property
    def empty(self) -> bool:
        return not (self.meal or self.drinks or self.activities or self.doses)

    def created(self) -> dict[str, Any]:
        """The ids an undo deletes, and how to put the supplement list back."""
        return {
            FOOD: [self.meal.meal_id] if self.meal else [],
            DRINK: [saved.entry.id for saved in self.drinks],
            ACTIVITY: [entry.id for entry in self.activities],
            SUPPLEMENT: [
                {
                    "id": dose.intake.id,
                    "entry_id": dose.entry.id,
                    "listed_before": dose.listed_before,
                }
                for dose, _, _ in self.doses
            ],
        }

    def reply(
        self, user_id: str, language: str, skipped: list[dict[str, str]]
    ) -> dict[str, Any]:
        saved = []
        if self.meal is not None:
            saved.append(_meal_item(self.meal))
        saved += [_drink_item(drink, language) for drink in self.drinks]
        saved += [_activity_item(entry, language) for entry in self.activities]
        saved += [
            _dose_item(dose, product, day_doses, user_id)
            for dose, product, day_doses in self.doses
        ]
        return {
            "saved": saved,
            "skipped": skipped,
            "nothing_found": not saved and not skipped,
        }


def _meal_item(meal: Meal) -> dict[str, Any]:
    calories = meal.nutrition.calories if meal.nutrition else 0.0
    return {
        "kind": FOOD,
        "id": meal.meal_id,
        "title": meal.dish_name or "",
        "summary": f"{calories:.0f} kcal",
        "estimated": False,
    }


def _drink_item(saved: SavedDrink, language: str) -> dict[str, Any]:
    payload = described_drink_payload(saved, language)
    return {
        "kind": DRINK,
        "id": saved.entry.id,
        "title": payload["name"],
        "summary": f"{saved.entry.volume_ml} ml",
        "estimated": False,
        "drink": payload,
    }


def _activity_item(entry: MovementEntry, language: str) -> dict[str, Any]:
    minutes = "phút" if language == "vi" else "min"
    return {
        "kind": ACTIVITY,
        "id": entry.id,
        "title": localized_activity_name(entry.activity_id, language)
        or entry.activity_name,
        "summary": f"{entry.duration_min} {minutes} · {entry.kcal_burned:.0f} kcal",
        "estimated": False,
        "activity": _movement_response(entry),
    }


def _dose_item(
    dose: SavedDose,
    product: SupplementProduct,
    day_doses: list[SupplementIntake],
    user_id: str,
) -> dict[str, Any]:
    return {
        "kind": SUPPLEMENT,
        "id": dose.intake.id,
        "title": product.name,
        "summary": f"{dose.intake.units:g} {product.unit_label}",
        "estimated": product.estimated,
        "intake": intake_payload(dose.intake),
        "entry": list_entry_payload(dose.entry, product, user_id, day_doses),
        "product": product_payload(product, user_id),
    }
