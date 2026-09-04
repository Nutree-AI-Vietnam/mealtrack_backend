"""Classify whether typed chat is in Nutree Coach scope. Pure. No I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.services.chat.intent_classification import fold_intent_text

SCOPE_IN = "in_scope"
SCOPE_OUT = "out_of_scope"
SCOPE_ACCEPT_THRESHOLD = 0.75
SCOPE_MARGIN = 0.08

# Multi-word / whole-token needles. fold_intent_text is applied at match time.
_OUT_PHRASES = (
    "write a python",
    "write python",
    "python function",
    "javascript function",
    "typescript",
    "debug this code",
    "debug my code",
    "leetcode",
    "stack overflow",
    "react component",
    "react native",
    "kubernetes",
    "docker compose",
    "python",
    "javascript",
    "write a function",
    "implement a class",
    "compile error",
    "git rebase",
    "how do i code",
    "write a program",
    "sql query",
    "algorithm for",
    "binary tree",
    "programming homework",
    "viet ham",
    "viet mot ham",
    "lap trinh",
    "viet code",
    "code giup toi",
    "stock price",
    "gia co phieu",
    "who won the",
    "what s the weather",
    "whats the weather",
    "what is the weather",
    "thoi tiet",
    "homework help",
    "write an essay",
    "viet bai luan",
    "explain kubernetes",
)

_IN_PHRASES = (
    "protein",
    "carb",
    "carbs",
    "carbohydrate",
    "calorie",
    "calories",
    "kcal",
    "calo",
    "macro",
    "macros",
    "fiber",
    "sugar",
    "hydration",
    "vitamin",
    "tdee",
    "fasting",
    "meal prep",
    "allergy",
    "allergies",
    "sodium",
    "cholesterol",
    "nutrition",
    "dinh duong",
    "chat dam",
    "chat bot",
    "chat beo",
    "chat xo",
    "bua an",
    "thuc an",
    "uong nuoc",
    "di ung",
    "nhin an",
    "logged",
    "dinner",
    "lunch",
    "breakfast",
    "snack",
    "supper",
    "meal",
    "hello",
    "hi",
    "hey",
    "xin chao",
    "chao coach",
)

SCOPE_EXEMPLARS: dict[str, tuple[str, ...]] = {
    SCOPE_IN: (
        "What's the function of protein?",
        "Cite protein guidance",
        "Is white rice okay for cutting?",
        "How much water should I drink?",
        "What is TDEE?",
        "I already logged dinner",
        "Explain my remaining macros",
        "Any meal prep tips for high protein?",
        "Does fiber help with fullness?",
        "Chất đạm để làm gì?",
        "Tôi nên uống bao nhiêu nước?",
        "Cơm trắng có ổn không?",
        "Tôi đã ghi bữa tối rồi",
    ),
    SCOPE_OUT: (
        "Write a Python function",
        "Debug this JavaScript error",
        "Explain Kubernetes pods",
        "Help with my programming homework",
        "What's the weather in Hanoi?",
        "What is the stock price of Apple?",
        "Write a React component",
        "How do I rebase in git?",
        "Viết hàm Python giúp tôi",
        "Thời tiết hôm nay thế nào?",
        "Giải thích kubernetes",
    ),
}


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    scope: str
    source: str
    scores: dict[str, float]
    margin: float = 0.0

    @property
    def in_scope(self) -> bool:
        return self.scope == SCOPE_IN


def phrase_scope(text: str) -> str | None:
    """Cheap whole-phrase match. Out-of-scope wins over nutrition tokens."""
    folded = fold_intent_text(text)
    if not folded:
        return None
    padded = f" {folded} "
    for phrase in _OUT_PHRASES:
        needle = fold_intent_text(phrase)
        if needle and f" {needle} " in padded:
            return SCOPE_OUT
    for phrase in _IN_PHRASES:
        needle = fold_intent_text(phrase)
        if needle and f" {needle} " in padded:
            return SCOPE_IN
    return None


def pick_scope(scores: Mapping[str, float]) -> ScopeDecision:
    """Reject only when out-of-scope clearly wins. Default is answer the question."""
    in_score = float(scores.get(SCOPE_IN, 0.0))
    out_score = float(scores.get(SCOPE_OUT, 0.0))
    margin = out_score - in_score
    if out_score >= SCOPE_ACCEPT_THRESHOLD and margin >= SCOPE_MARGIN:
        return ScopeDecision(SCOPE_OUT, "embedding", dict(scores), margin)
    return ScopeDecision(SCOPE_IN, "embedding", dict(scores), in_score - out_score)
