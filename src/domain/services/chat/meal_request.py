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

# Imperative changes to a meal already on screen. How/why questions stay prose.
_REFINEMENT_PHRASES: tuple[str, ...] = (
    "make it",
    "make the",
    "make them",
    "make this",
    "skip the",
    "skip rice",
    "without the",
    "without rice",
    "without dairy",
    "without eggs",
    "without meat",
    "without gluten",
    "higher protein",
    "more protein",
    "less carb",
    "lower calorie",
    "fewer calorie",
    "spicier",
    "vegetarian",
    "vegan",
    "swap the",
    "instead of",
    "another version",
    "leave out",
    "don't include",
    "do not include",
    "i don't want",
    "less rice",
    "more vegetables",
    "đổi món",
    "doi mon",
    "đổi sang",
    "doi sang",
    "bỏ cơm",
    "bo com",
    "không cơm",
    "khong com",
    "không có",
    "khong co",
    "nhiều đạm",
    "nhieu dam",
    "ít tinh bột",
    "it tinh bot",
    "món chay",
    "mon chay",
    "cay hơn",
    "cay hon",
    "thay cơm",
    "thay com",
    "thay món",
    "thay mon",
)

_EXPLAIN_PREFIXES: tuple[str, ...] = (
    "why ",
    "how ",
    "tại sao",
    "tai sao",
    "vì sao",
    "vi sao",
    "làm sao",
    "lam sao",
)

# How/why questions that do not start the message ("Can you explain why...").
_EXPLAIN_MARKERS: tuple[str, ...] = (
    "explain why",
    "explain how",
    "how do i",
    "how to ",
    "how long",
    "what's in",
    "what is in",
    "cách nấu",
    "cach nau",
    "cách làm",
    "cach lam",
    "giải thích",
    "giai thich",
)

# Comparison questions mention the same words as a change request
# ("higher protein") but should stay a written answer.
_QUESTION_MARKERS: tuple[str, ...] = (
    "which ",
    "compare ",
    "difference",
    "between the",
    "is the ",
    "is it ",
    "are these ",
    "are they ",
    "cái nào",
    "cai nao",
    "món nào",
    "mon nao",
    "so sánh",
    "so sanh",
)

_IMPERATIVE_HINTS: tuple[str, ...] = (
    "make ",
    "skip ",
    "swap ",
    "leave out",
    "don't ",
    "do not ",
    "without ",
    "instead of",
    "đổi",
    "doi ",
    "bỏ",
    "thay ",
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


def is_meal_comparison(text: str) -> bool:
    """True when the user is asking about meals already shown, not changing them."""
    normalized = _normalize(text)
    if not normalized or is_meal_refinement(text):
        return False
    return any(marker in normalized for marker in _QUESTION_MARKERS)


_COOK_MARKERS: tuple[str, ...] = (
    "cook",
    "recipe",
    "cách nấu",
    "cach nau",
    "cách làm",
    "cach lam",
    "công thức",
    "cong thuc",
    "nấu món",
    "nau mon",
)


def is_cook_request(text: str) -> bool:
    """True when the user wants the shown meal's recipe, not a new dish."""
    normalized = _normalize(text)
    if not normalized or is_meal_refinement(text) or is_meal_suggestion_request(text):
        return False
    return any(marker in normalized for marker in _COOK_MARKERS)


def is_meal_explanation(text: str) -> bool:
    """True when the user wants the shown meal explained, not replaced.

    How/why questions already skip the refinement path. They still need their
    own check so the model is not offered a tool that fetches new cards.
    """
    normalized = _normalize(text)
    if not normalized or is_meal_refinement(text):
        return False
    if any(normalized.startswith(prefix) for prefix in _EXPLAIN_PREFIXES):
        return True
    return any(marker in normalized for marker in _EXPLAIN_MARKERS)


def is_meal_refinement(text: str) -> bool:
    """True when the user wants the current meal changed, not explained."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(normalized.startswith(prefix) for prefix in _EXPLAIN_PREFIXES):
        return False
    if any(marker in normalized for marker in _QUESTION_MARKERS) and not any(
        hint in normalized for hint in _IMPERATIVE_HINTS
    ):
        return False
    return any(phrase in normalized for phrase in _REFINEMENT_PHRASES)


def _normalize(text: str) -> str:
    folded = text.casefold().replace("’", "'").replace("`", "'")
    return " ".join(folded.split())
