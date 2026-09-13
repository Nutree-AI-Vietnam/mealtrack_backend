"""Unit tests for the Coach function registry and alias map."""

from __future__ import annotations

from src.domain.services.chat.coach_functions import (
    FUNCTION_CHECK_DAILY_PROGRESS,
    FUNCTION_EXPLAIN_LIMITS,
    FUNCTION_SEARCH_KNOWLEDGE,
    FUNCTION_SUGGEST_NEXT_MEAL,
    needs_retrieval,
    openai_tools,
    preferred_alias,
    resolve_coach_function,
)


class TestResolveCoachFunction:
    def test_remaining_budget_alias(self):
        resolved = resolve_coach_function("remaining_budget")
        assert resolved is not None
        assert resolved.name == FUNCTION_CHECK_DAILY_PROGRESS
        assert resolved.args == {"focus": "remaining_budget"}

    def test_day_progress_alias(self):
        resolved = resolve_coach_function("day_progress")
        assert resolved is not None
        assert resolved.name == FUNCTION_CHECK_DAILY_PROGRESS
        assert resolved.args == {"focus": "day_progress"}

    def test_next_meal_alias(self):
        resolved = resolve_coach_function("next_meal")
        assert resolved is not None
        assert resolved.name == FUNCTION_SUGGEST_NEXT_MEAL
        assert resolved.args == {}

    def test_limits_alias(self):
        resolved = resolve_coach_function("limits")
        assert resolved is not None
        assert resolved.name == FUNCTION_EXPLAIN_LIMITS
        assert resolved.args == {}

    def test_function_name_check_daily_progress_defaults_focus(self):
        resolved = resolve_coach_function("check_daily_progress")
        assert resolved is not None
        assert resolved.name == FUNCTION_CHECK_DAILY_PROGRESS
        assert resolved.args == {"focus": "remaining_budget"}

    def test_function_name_search_knowledge(self):
        resolved = resolve_coach_function("search_nutrition_knowledge")
        assert resolved is not None
        assert resolved.name == FUNCTION_SEARCH_KNOWLEDGE
        assert resolved.args == {}

    def test_unknown_returns_none(self):
        assert resolve_coach_function("not_a_real_function") is None

    def test_empty_returns_none(self):
        assert resolve_coach_function(None) is None
        assert resolve_coach_function("") is None
        assert resolve_coach_function("   ") is None


class TestPreferredAlias:
    def test_progress_focus_round_trips(self):
        assert (
            preferred_alias(FUNCTION_CHECK_DAILY_PROGRESS, {"focus": "day_progress"})
            == "day_progress"
        )
        assert (
            preferred_alias(
                FUNCTION_CHECK_DAILY_PROGRESS, {"focus": "remaining_budget"}
            )
            == "remaining_budget"
        )

    def test_search_knowledge_has_no_chip_alias(self):
        assert preferred_alias(FUNCTION_SEARCH_KNOWLEDGE, {}) is None

    def test_next_meal_and_limits_aliases(self):
        assert preferred_alias(FUNCTION_SUGGEST_NEXT_MEAL, {}) == "next_meal"
        assert preferred_alias(FUNCTION_EXPLAIN_LIMITS, {}) == "limits"


class TestNeedsRetrieval:
    def test_progress_limits_next_meal_skip_retrieve(self):
        assert needs_retrieval(FUNCTION_CHECK_DAILY_PROGRESS) is False
        assert needs_retrieval(FUNCTION_EXPLAIN_LIMITS) is False
        assert needs_retrieval(FUNCTION_SUGGEST_NEXT_MEAL) is False

    def test_knowledge_search_retrieves(self):
        assert needs_retrieval(FUNCTION_SEARCH_KNOWLEDGE) is True


class TestOpenaiTools:
    def test_offers_four_tools_with_registry_names(self):
        tools = openai_tools()
        names = [tool["function"]["name"] for tool in tools]
        assert names == [
            FUNCTION_SUGGEST_NEXT_MEAL,
            FUNCTION_CHECK_DAILY_PROGRESS,
            FUNCTION_SEARCH_KNOWLEDGE,
            FUNCTION_EXPLAIN_LIMITS,
        ]
        assert all(tool["type"] == "function" for tool in tools)
