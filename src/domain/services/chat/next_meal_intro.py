"""Short next-meal reply built from cards the server already verified."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.services.chat.meal_request import is_meal_refinement

_SLOT_LABELS = {
    "breakfast": ("bữa sáng", "breakfast"),
    "lunch": ("bữa trưa", "lunch"),
    "dinner": ("bữa tối", "dinner"),
    "snack": ("bữa phụ", "a snack"),
}


def next_meal_ack(*, locale: str, slot: str) -> str:
    """First line while cards are still being chosen. No nutrition numbers."""
    vi = locale == "vi"
    label = _slot_label(slot, vi=vi)
    if vi:
        return f"Mình đang chọn {label} vừa với phần còn lại."
    if slot == "snack":
        return "I'm picking a snack that fits what's left."
    return f"I'm picking {label} that fits what's left."


def next_meal_intro(
    suggestions: Sequence[Mapping[str, Any]],
    *,
    locale: str,
    slot: str,
    user_text: str | None = None,
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
            body = (
                f"{featured} hợp cho {slot_label}. "
                f"Bạn cũng có thể chọn {_join(others, vi=True)}. "
                "Chạm một thẻ để xem công thức, hoặc ghi bữa ngay trên thẻ."
            )
        else:
            body = (
                f"{featured} hợp cho {slot_label}. "
                "Chạm thẻ để xem công thức, hoặc ghi bữa ngay trên thẻ."
            )
    elif others:
        body = (
            f"{featured} fits {slot_label}. "
            f"You can also choose {_join(others, vi=False)}. "
            "Tap a card for the recipe, or log it from the card."
        )
    else:
        body = (
            f"{featured} fits {slot_label}. "
            "Tap the card for the recipe, or log it from the card."
        )
    lead = _change_lead(user_text, vi=vi)
    if lead is None:
        return body
    return f"{lead} {body}"


# Specific change, then a short reply. First match wins. No nutrition numbers.
_CHANGE_LEADS: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        ("higher protein", "more protein", "nhiều đạm", "nhieu dam"),
        ("These are the higher-protein options.", "Đây là các món nhiều đạm hơn."),
    ),
    (
        (
            "skip the rice",
            "skip rice",
            "without rice",
            "less rice",
            "bỏ cơm",
            "bo com",
            "không cơm",
            "khong com",
        ),
        ("These leave out the rice.", "Những món này không có cơm."),
    ),
    (
        ("vegetarian", "vegan", "món chay", "mon chay"),
        ("These are vegetarian.", "Đây là món chay."),
    ),
    (
        ("spicier", "cay hơn", "cay hon"),
        ("These are spicier.", "Những món này cay hơn."),
    ),
    (
        ("without dairy",),
        ("These leave out dairy.", "Những món này không có sữa."),
    ),
)


def _change_lead(user_text: str | None, *, vi: bool) -> str | None:
    if not user_text or not is_meal_refinement(user_text):
        return None
    folded = " ".join(user_text.casefold().replace("’", "'").split())
    for phrases, labels in _CHANGE_LEADS:
        if any(phrase in folded for phrase in phrases):
            return labels[1] if vi else labels[0]
    if vi:
        return "Mình đã đổi theo yêu cầu của bạn."
    return "I changed these to match what you asked."


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
