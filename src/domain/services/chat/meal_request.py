"""Detect free-text requests for a meal suggestion. Pure. No I/O."""

from __future__ import annotations

from src.domain.services.chat.meal_slot import slot_from_user_text

# Phrases that ask for food to eat, in English and Vietnamese (with and
# without diacritics). Matched on normalized text, not as the whole message.
_PHRASES: tuple[str, ...] = (
    "what should i eat",
    "what can i eat",
    "what do i eat",
    "what to eat",
    "what's for",
    "whats for",
    "what is for",
    "something to eat",
    "meal idea",
    "meal ideas",
    "recipe idea",
    "suggest a meal",
    "suggest a snack",
    "suggest a breakfast",
    "suggest a lunch",
    "suggest a dinner",
    "suggest a recipe",
    "i'm hungry",
    "i am hungry",
    "im hungry",
    "next meal",
    "ăn gì",
    "an gi",
    "nên ăn gì",
    "nen an gi",
    "gợi ý bữa",
    "goi y bua",
    "gợi ý món",
    "goi y mon",
    "món gì",
    "mon gi",
    "đói bụng",
    "doi bung",
)

# A named slot plus one of these is a suggestion request ("more breakfast ideas")
# without also matching nutrition questions that merely mention dinner.
_SLOT_REQUEST_TOKENS: tuple[str, ...] = (
    "idea",
    "ideas",
    "suggest",
    "recommend",
    "recipe",
    "gợi ý",
    "goi y",
    "i want",
    "i'd like",
    "id like",
)


def is_meal_suggestion_request(text: str) -> bool:
    """True when free text is asking what to eat, not for a nutrition fact."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _PHRASES):
        return True
    if slot_from_user_text(normalized) is None:
        return False
    return any(token in normalized for token in _SLOT_REQUEST_TOKENS)


def _normalize(text: str) -> str:
    folded = text.casefold().replace("’", "'").replace("`", "'")
    return " ".join(folded.split())
