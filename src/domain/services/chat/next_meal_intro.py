"""Short next-meal reply built from cards the server already verified."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.services.chat.meal_request import is_cook_request, is_meal_refinement

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


def verified_cook_reply(
    user_text: str,
    cards: Sequence[Mapping[str, Any]],
    *,
    locale: str,
) -> str | None:
    """Recipe steps copied from the card the user asked how to cook.

    Returns None when the question is not a cook request, or when no shown
    card has steps. The text has no calorie or macro figures added by us.
    """
    if not is_cook_request(user_text):
        return None
    usable = [card for card in cards if _recipe_lines(card)]
    if not usable:
        return None
    if _names_a_different_dish(user_text, usable):
        return None
    card = _card_named_in_text(user_text, usable) or usable[0]
    lines = _recipe_lines(card)
    name = " ".join(str(card.get("name") or "").split())
    body = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))
    if locale == "vi":
        lead = f"Cách làm {name}:" if name else "Cách làm:"
    else:
        lead = f"How to make {name}:" if name else "How to make it:"
    return f"{lead}\n{body}"


def cook_reply_already_said(text: str, reply: str) -> bool:
    """True when the model answer already includes the card's first step."""
    first = ""
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped[:1].isdigit() and ". " in stripped:
            first = stripped.split(". ", 1)[1].strip().rstrip(".")
            break
    if not first:
        return False
    return _fold(first) in _fold(text)


def next_meal_unavailable(*, locale: str, slot: str) -> str:
    """Shown when a meal was requested and no card came back.

    No dish name and no nutrition numbers, so the reply cannot invent a meal.
    """
    vi = locale == "vi"
    label = _slot_label(slot, vi=vi)
    if vi:
        return (
            f"Mình chưa tìm được {label} vừa với phần còn lại. "
            "Bạn thử bữa khác, hoặc nói mình cần đổi gì."
        )
    if slot == "snack":
        noun = "a snack"
    elif slot in _SLOT_LABELS:
        noun = f"a {label}"
    else:
        noun = "a meal"
    return (
        f"I couldn't find {noun} that fits what's left. "
        "Try another meal, or tell me what to change."
    )


def eaten_today_name(
    meals: Sequence[Any],
    *,
    local_date: str | None,
    suggestion_names: Sequence[str],
) -> str | None:
    """Newest dish logged today that is not already one of the cards.

    Meals are oldest-first. A card with the same name is skipped so the reply
    does not say the dish was eaten and then offer it again. No nutrition numbers.
    """
    if not local_date:
        return None
    offered = {_fold(name) for name in suggestion_names}
    offered.discard("")
    chosen: str | None = None
    for meal in meals:
        if isinstance(meal, Mapping):
            logged_on = meal.get("local_date")
            raw_name = meal.get("dish_name")
        else:
            logged_on = getattr(meal, "local_date", None)
            raw_name = getattr(meal, "dish_name", None)
        if logged_on != local_date:
            continue
        label = _dish_label(raw_name)
        if label is None or _fold(label) in offered:
            continue
        chosen = label
    return chosen


def with_eaten_today(text: str, eaten: str | None, *, locale: str) -> str:
    """Name today's logged dish when a usable model sentence left it out."""
    label = _dish_label(eaten)
    cleaned = " ".join(text.split())
    if label is None or not cleaned:
        return cleaned
    if _fold(label) in _fold(cleaned):
        return cleaned
    if locale == "vi":
        lead = f"Hôm nay bạn đã ghi {label}."
    else:
        lead = f"You already logged {label} today."
    return f"{lead} {cleaned}"


def next_meal_intro(
    suggestions: Sequence[Mapping[str, Any]],
    *,
    locale: str,
    slot: str,
    user_text: str | None = None,
    eaten_today: str | None = None,
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
    parts: list[str] = []
    lead = _change_lead(user_text, vi=vi)
    if lead is not None:
        parts.append(lead)
    eaten = _dish_label(eaten_today)
    if eaten is not None and _fold(eaten) not in {_fold(name) for name in names}:
        if vi:
            parts.append(f"Hôm nay bạn đã ghi {eaten}.")
        else:
            parts.append(f"You already logged {eaten} today.")
    parts.append(body)
    return " ".join(parts)


def _dish_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 40:
        return None
    return cleaned


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


def _recipe_lines(card: Mapping[str, Any]) -> list[str]:
    raw = card.get("recipe_steps")
    if not isinstance(raw, list):
        return []
    lines: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = item.get("instruction") or item.get("text") or ""
        elif isinstance(item, str):
            text = item
        else:
            continue
        cleaned = " ".join(str(text).split())
        if not cleaned:
            continue
        lines.append(cleaned[:160])
        if len(lines) >= 4:
            break
    return lines


def _card_named_in_text(
    user_text: str, cards: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    for card in cards:
        name = " ".join(str(card.get("name") or "").split())
        if _name_in_text(name, user_text):
            return card
    return None


def _name_in_text(name: str, text: str) -> bool:
    folded_name = _fold(name)
    folded_text = _fold(text)
    if not folded_name:
        return False
    if folded_name in folded_text:
        return True
    words = folded_name.split()
    token = " ".join(words[:2]) if len(words) >= 2 and len(words[0]) < 4 else words[0]
    return len(token) >= 4 and token in folded_text


_COOK_NOISE = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "meal",
        "what",
        "whats",
        "how",
        "does",
        "make",
        "cook",
        "recipe",
        "please",
        "cach",
        "làm",
        "lam",
        "nấu",
        "nau",
        "món",
        "mon",
        "này",
        "nay",
        "the",
        "for",
        "can",
        "you",
        "and",
    }
)


def _names_a_different_dish(user_text: str, cards: Sequence[Mapping[str, Any]]) -> bool:
    """True when the question names a dish that is not one of the cards."""
    if _card_named_in_text(user_text, cards) is not None:
        return False
    folded = _fold(user_text)
    for marker in (
        "cook",
        "recipe",
        "cách nấu",
        "cach nau",
        "cách làm",
        "cach lam",
        "công thức",
        "cong thuc",
    ):
        folded = folded.replace(marker, " ")
    names = _fold(" ".join(str(card.get("name") or "") for card in cards))
    for word in folded.split():
        token = "".join(char for char in word if char.isalpha())
        if len(token) < 3 or token in _COOK_NOISE or token in names:
            continue
        return True
    return False


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
