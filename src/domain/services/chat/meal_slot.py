"""Local clock → meal slot. Pure. No I/O."""

from __future__ import annotations

from datetime import datetime

_SLOTS = ("breakfast", "lunch", "snack", "dinner")


def suggested_meal_slot(hour: int, minute: int) -> str:
    """Map local clock time onto breakfast/lunch/snack/dinner."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("hour must be 0-23 and minute 0-59")
    minutes = hour * 60 + minute
    if 5 * 60 <= minutes <= 10 * 60 + 29:
        return "breakfast"
    if 10 * 60 + 30 <= minutes <= 14 * 60 + 29:
        return "lunch"
    if 14 * 60 + 30 <= minutes <= 16 * 60 + 59:
        return "snack"
    if 17 * 60 <= minutes <= 21 * 60 + 59:
        return "dinner"
    return "snack"


def meal_portion_type_for_slot(slot: str) -> str:
    """Discover portion: snack slot is snack-sized; meals are main."""
    return "snack" if slot == "snack" else "main"


def slot_from_local_datetime(now: datetime) -> tuple[int, int, str]:
    """Return (hour, minute, slot) from a timezone-aware local datetime."""
    hour = now.hour
    minute = now.minute
    return hour, minute, suggested_meal_slot(hour, minute)


def is_known_slot(value: str | None) -> bool:
    return value in _SLOTS


_TEXT_SLOT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "breakfast",
        ("breakfast", "bữa sáng", "bua sang"),
    ),
    (
        "lunch",
        ("lunch", "bữa trưa", "bua trua"),
    ),
    (
        "dinner",
        ("dinner", "supper", "bữa tối", "bua toi", "tối nay", "toi nay"),
    ),
    (
        "snack",
        ("snack", "bữa phụ", "bua phu", "ăn vặt", "an vat"),
    ),
)


def slot_from_user_text(text: str) -> str | None:
    """Last explicit meal-slot mention in user text wins over the clock."""
    lowered = text.casefold()
    last_index = -1
    last_slot: str | None = None
    for slot, tokens in _TEXT_SLOT_TOKENS:
        for token in tokens:
            index = lowered.rfind(token)
            if index > last_index:
                last_index = index
                last_slot = slot
    return last_slot


def resolve_meal_slot(suggested_slot: str | None, user_text: str) -> str:
    """Typed/chip slot overrides the local clock; otherwise use suggested or snack."""
    return slot_from_user_text(user_text) or (
        suggested_slot if is_known_slot(suggested_slot) else "snack"
    )
