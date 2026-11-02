"""Saved suggestions read back in the shape the app's suggestion model parses."""

from datetime import UTC, datetime
from typing import Any

from src.infra.database.models.saved_suggestion import SavedSuggestionModel
from src.infra.repositories.saved_suggestion_normalization import (
    apply_normalized_saved_suggestion_fields,
    project_saved_suggestion_data,
)


def _project(data: dict[str, Any]) -> dict[str, Any]:
    model = SavedSuggestionModel(suggestion_data=data, saved_at=datetime.now(UTC))
    apply_normalized_saved_suggestion_fields(model, data)
    return project_saved_suggestion_data(model)


def test_ingredients_keep_amount_and_a_string_unit():
    projected = _project(
        {
            "id": "s1",
            "meal_name": "Pho",
            "macros": {"calories": 500, "protein": 30, "carbs": 60, "fat": 10},
            "ingredients": [
                {"name": "beef", "amount": 150, "unit": "g", "calories": 300},
                {"name": "herbs", "quantity": 1},
            ],
        }
    )

    beef, herbs = projected["ingredients"]
    assert (beef["amount"], beef["quantity"], beef["unit"]) == (150.0, 150.0, "g")
    assert beef["calories"] == 300
    assert (herbs["amount"], herbs["unit"]) == (1.0, "")


def test_snapshot_fields_attach_only_when_the_lists_line_up():
    projected = _project(
        {
            "ingredients": [
                {"name": "beef", "amount": 150, "unit": "g", "note": "sliced"},
                {"name": "", "amount": 1},
                "salt",
            ]
        }
    )

    names = [item["name"] for item in projected["ingredients"]]
    assert names == ["beef", "salt"]
    assert "note" not in projected["ingredients"][0]
    assert projected["ingredients"][1]["amount"] == 0.0
