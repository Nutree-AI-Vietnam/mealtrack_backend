"""Classify typed chat into a ChatIntent. Pure. No I/O."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.domain.model.chat import ChatIntent
from src.domain.services.chat.meal_slot import slot_from_user_text

INTENT_ACCEPT_THRESHOLD = 0.70
NEXT_MEAL_ACCEPT_THRESHOLD = 0.75
INTENT_MARGIN = 0.08
BUDGET_INTENT_MARGIN = 0.04

_BUDGET_INTENTS = frozenset(
    {ChatIntent.REMAINING_BUDGET.value, ChatIntent.DAY_PROGRESS.value}
)

_ASK_MEAL = (
    "what should i eat",
    "whats for",
    "what's for",
    "an gi",
    "nen an",
    "goi y",
    "ideas",
    "suggest",
    "recommend",
)

INTENT_EXEMPLARS: dict[str, tuple[str, ...]] = {
    ChatIntent.REMAINING_BUDGET.value: (
        "What's left in my day?",
        "How much is left?",
        "How many calories do I have left?",
        "How much have I eaten?",
        "What's my remaining budget?",
        "Remaining macros today",
        "Calories left for today",
        "How far am I from my calorie cap?",
        "Hôm nay tôi còn bao nhiêu?",
        "Tôi đã ăn bao nhiêu rồi?",
        "Còn lại bao nhiêu kcal?",
        "Hôm nay còn bao nhiêu calo?",
        "Macro còn lại",
    ),
    ChatIntent.DAY_PROGRESS.value: (
        "How's my day going?",
        "Am I on track today?",
        "Did I go over today?",
        "How is today looking?",
        "Am I over budget?",
        "Hôm nay tôi thế nào?",
        "Hôm nay tôi có đang ổn không?",
        "Hôm nay vượt chưa?",
        "Hôm nay ăn có ổn không?",
    ),
    ChatIntent.NEXT_MEAL.value: (
        "What should I eat next?",
        "What should I eat?",
        "What's for dinner?",
        "What's for lunch?",
        "What's for breakfast?",
        "Suggest a snack",
        "Meal ideas that fit",
        "Any supper recommendations",
        "Plan lunch",
        "Tôi nên ăn gì tiếp?",
        "Ăn gì tối nay?",
        "Gợi ý bữa trưa",
        "Gợi ý bữa nhẹ",
        "Nên ăn gì?",
        "Ăn gì tiếp theo?",
    ),
    ChatIntent.LIMITS.value: (
        "What Coach can't do",
        "What can you do?",
        "Can you log this meal?",
        "Change my calorie target",
        "Can you edit my log?",
        "Bạn làm được gì?",
        "Bạn không làm được gì?",
        "Log giúp tôi được không?",
        "Đổi mục tiêu giúp tôi",
    ),
}


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: str | None
    source: str
    scores: dict[str, float]
    margin: float = 0.0


def fold_intent_text(text: str) -> str:
    """Casefold, strip punctuation, and drop Vietnamese diacritics."""
    lowered = text.replace("đ", "d").replace("Đ", "d").casefold()
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(char)
    )
    cleaned = "".join(
        char if char.isalnum() or char.isspace() else " " for char in stripped
    )
    return " ".join(cleaned.split())


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def phrase_intent(text: str) -> str | None:
    """Cheap exact/contains match. Used when embeddings are missing or tied."""
    folded = fold_intent_text(text)
    if not folded:
        return None
    for intent, phrases in INTENT_EXEMPLARS.items():
        for phrase in phrases:
            needle = fold_intent_text(phrase)
            if needle and needle in folded:
                return intent
    if slot_from_user_text(text) and any(
        fold_intent_text(token) in folded for token in _ASK_MEAL
    ):
        return ChatIntent.NEXT_MEAL.value
    return None


def pick_intent(scores: Mapping[str, float]) -> IntentDecision:
    """Accept a winner only when score and margin clear the bar.

    next_meal is stricter: a false card fetch is a 45s quota hit.
    remaining_budget vs day_progress may be close; a thinner margin is ok.
    """
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return IntentDecision(None, "none", {})
    top_intent, top_score = ranked[0]
    second_intent, second_score = ranked[1] if len(ranked) > 1 else (None, 0.0)
    margin = top_score - second_score
    threshold = (
        NEXT_MEAL_ACCEPT_THRESHOLD
        if top_intent == ChatIntent.NEXT_MEAL.value
        else INTENT_ACCEPT_THRESHOLD
    )
    min_margin = (
        BUDGET_INTENT_MARGIN
        if {top_intent, second_intent} <= _BUDGET_INTENTS
        else INTENT_MARGIN
    )
    if top_score < threshold or margin < min_margin:
        return IntentDecision(None, "none", dict(scores), margin)
    return IntentDecision(top_intent, "embedding", dict(scores), margin)


def score_intents(
    query_embedding: Sequence[float],
    exemplar_embeddings: Mapping[str, Sequence[Sequence[float]]],
) -> dict[str, float]:
    """Max cosine of the query against each intent's exemplars."""
    scores: dict[str, float] = {}
    for intent, vectors in exemplar_embeddings.items():
        if not vectors:
            scores[intent] = 0.0
            continue
        scores[intent] = max(
            cosine_similarity(query_embedding, vector) for vector in vectors
        )
    return scores
