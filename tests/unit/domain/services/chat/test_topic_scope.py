from src.domain.services.chat.topic_scope import (
    SCOPE_IN,
    SCOPE_OUT,
    phrase_scope,
    pick_scope,
)


def test_phrase_rejects_coding() -> None:
    assert phrase_scope("Write a Python function") == SCOPE_OUT
    assert phrase_scope("Viết hàm Python giúp tôi") == SCOPE_OUT


def test_phrase_keeps_nutrition_free_text() -> None:
    assert phrase_scope("Cite protein guidance") == SCOPE_IN
    assert phrase_scope("What's the function of protein?") == SCOPE_IN
    assert phrase_scope("I already logged dinner") == SCOPE_IN


def test_coding_about_calories_is_still_out() -> None:
    assert phrase_scope("Write a Python function to calculate TDEE") == SCOPE_OUT


def test_logged_dinner_is_not_coding() -> None:
    assert phrase_scope("I already logged dinner") != SCOPE_OUT


def test_pick_scope_rejects_only_when_out_wins_clearly() -> None:
    accepted = pick_scope({SCOPE_OUT: 0.86, SCOPE_IN: 0.70})
    assert accepted.scope == SCOPE_OUT
    assert accepted.source == "embedding"

    defaulted = pick_scope({SCOPE_OUT: 0.80, SCOPE_IN: 0.79})
    assert defaulted.scope == SCOPE_IN


def test_pick_scope_defaults_in_when_scores_are_weak() -> None:
    decision = pick_scope({SCOPE_OUT: 0.40, SCOPE_IN: 0.30})
    assert decision.scope == SCOPE_IN
