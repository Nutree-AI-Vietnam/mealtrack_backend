from src.domain.services.chat.intent_classification import (
    cosine_similarity,
    fold_intent_text,
    phrase_intent,
    pick_intent,
    score_intents,
)


def test_fold_strips_vietnamese_diacritics() -> None:
    assert fold_intent_text("Tôi đã ăn bao nhiêu rồi?") == "toi da an bao nhieu roi"


def test_phrase_remaining_budget_vietnamese() -> None:
    assert phrase_intent("Tôi đã ăn bao nhiêu rồi?") == "remaining_budget"
    assert phrase_intent("toi da an bao nhieu roi") == "remaining_budget"


def test_phrase_next_meal_dinner() -> None:
    assert phrase_intent("What's for dinner?") == "next_meal"
    assert phrase_intent("ăn gì tối nay") == "next_meal"


def test_phrase_typed_chip_labels() -> None:
    assert phrase_intent("How's my day going?") == "day_progress"
    assert phrase_intent("What should I eat next?") == "next_meal"
    assert phrase_intent("What's left in my day?") == "remaining_budget"


def test_logged_dinner_is_not_next_meal() -> None:
    assert phrase_intent("I already logged dinner") is None


def test_cosine_identical_and_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_pick_intent_requires_margin() -> None:
    decision = pick_intent(
        {"next_meal": 0.91, "remaining_budget": 0.90, "day_progress": 0.1}
    )
    assert decision.intent is None
    assert decision.source == "none"


def test_pick_intent_next_meal_needs_higher_score() -> None:
    decision = pick_intent(
        {"next_meal": 0.72, "remaining_budget": 0.10, "day_progress": 0.10}
    )
    assert decision.intent is None


def test_pick_intent_accepts_clear_next_meal() -> None:
    decision = pick_intent(
        {"next_meal": 0.76, "remaining_budget": 0.20, "day_progress": 0.10}
    )
    assert decision.intent == "next_meal"
    assert decision.source == "embedding"


def test_pick_intent_accepts_budget_at_seventy() -> None:
    decision = pick_intent(
        {"remaining_budget": 0.71, "day_progress": 0.20, "next_meal": 0.10}
    )
    assert decision.intent == "remaining_budget"


def test_pick_intent_allows_close_budget_pair() -> None:
    decision = pick_intent(
        {"remaining_budget": 0.86, "day_progress": 0.81, "next_meal": 0.1}
    )
    assert decision.intent == "remaining_budget"


def test_score_intents_uses_max_cosine() -> None:
    scores = score_intents(
        [1.0, 0.0],
        {
            "next_meal": ([1.0, 0.0], [0.0, 1.0]),
            "remaining_budget": ([0.0, 1.0],),
        },
    )
    assert scores["next_meal"] == 1.0
    assert scores["remaining_budget"] == 0.0
