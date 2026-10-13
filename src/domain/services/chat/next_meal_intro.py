"""Short next-meal reply built from cards the server already verified."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SLOT_LABELS = {
    "breakfast": ("bữa sáng", "breakfast"),
    "lunch": ("bữa trưa", "lunch"),
    "dinner": ("bữa tối", "dinner"),
    "snack": ("bữa phụ", "a snack"),
}


def next_meal_intro(
    suggestions: Sequence[Mapping[str, Any]],
    *,
    locale: str,
    slot: str,
) -> str | None:
    """Name the featured meal and the other choices. Do not include calories."""
    names = [
        str(card.get("name")).strip()
        for card in suggestions
        if isinstance(card, Mapping) and str(card.get("name") or "").strip()
    ]
    if not names:
        return None
    featured = names[0]
    others = names[1:3]
    vi = locale == "vi"
    slot_label = _slot_label(slot, vi=vi)
    if vi:
        if others:
            return (
                f"{featured} hợp cho {slot_label}. "
                f"Bạn cũng có thể chọn {_join(others, vi=True)}. "
                "Chạm một thẻ để xem công thức và ghi bữa."
            )
        return f"{featured} hợp cho {slot_label}. Chạm thẻ để xem công thức và ghi bữa."
    if others:
        return (
            f"{featured} fits {slot_label}. "
            f"You can also choose {_join(others, vi=False)}. "
            "Tap a card to see the recipe and log it."
        )
    return f"{featured} fits {slot_label}. Tap the card to see the recipe and log it."


def _slot_label(slot: str, *, vi: bool) -> str:
    labels = _SLOT_LABELS.get(slot)
    if labels is None:
        return "bữa này" if vi else "this meal"
    return labels[0] if vi else labels[1]


def _join(names: Sequence[str], *, vi: bool) -> str:
    if len(names) == 1:
        return names[0]
    conjunction = " và " if vi else " and "
    return f"{names[0]}{conjunction}{names[1]}"
