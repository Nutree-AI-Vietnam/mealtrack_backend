"""Item identity, portion, and selected-reference scoring helpers."""

from __future__ import annotations

import re
from typing import Any

from .meal_text_nutrition_eval_metrics import interval_error


def score_items(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    selected_ids: tuple[str | None, ...],
    selected_id: str | None,
):
    unmatched = list(expected)
    identity_pass = quantity_pass = candidate_pass = True
    errors: list[float] = []
    for index, actual_item in enumerate(actual):
        identity = normalize(
            str(
                actual_item.get("lookup_name")
                or actual_item.get("canonical_name")
                or actual_item.get("name")
                or ""
            )
        )
        match = next(
            (
                item
                for item in unmatched
                if identity
                in {normalize(str(alias)) for alias in item.get("aliases", [])}
            ),
            None,
        )
        if match is None:
            identity_pass = quantity_pass = candidate_pass = False
            continue
        unmatched.remove(match)
        amount = actual_item.get("quantity_g")
        if amount is None:
            quantity_pass = False
        else:
            error = interval_error(float(amount), match.get("quantity_g", 0.0))
            errors.append(error)
            quantity_pass = quantity_pass and error <= max(1.0, float(amount) * 0.01)
        expected_id = match.get("candidate_id")
        actual_id = actual_item.get("candidate_id")
        if actual_id is None and len(expected) == 1:
            actual_id = (
                selected_ids[index] if index < len(selected_ids) else selected_id
            )
        if expected_id is not None:
            candidate_pass = candidate_pass and actual_id == expected_id
    if unmatched:
        identity_pass = quantity_pass = candidate_pass = False
    return identity_pass, quantity_pass, candidate_pass, errors


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", value.casefold())).strip()
