from unittest.mock import AsyncMock

import pytest

from src.infra.adapters.openai_chat_completion_adapter import (
    OpenAIChatCompletionAdapter,
    _structured_payload,
)


def test_structured_payload_unwraps_include_raw_dict() -> None:
    assert _structured_payload({"parsed": {"follow_ups": []}, "raw": object()}) == {
        "follow_ups": []
    }
    assert _structured_payload(
        {"follow_ups": [{"label": "x", "action": "limits"}]}
    ) == {"follow_ups": [{"label": "x", "action": "limits"}]}


@pytest.mark.asyncio
async def test_generate_follow_ups_sanitizes_dict_schema_payload() -> None:
    adapter = OpenAIChatCompletionAdapter(api_key="test", timeout_seconds=8)
    structured = AsyncMock()
    structured.ainvoke.return_value = {
        "follow_ups": [
            {"label": "What's left?", "action": "remaining_budget"},
            {"label": "What can you do?", "action": "limits"},
        ]
    }

    class _Llm:
        def with_structured_output(self, schema, **kwargs):
            del schema, kwargs
            return structured

    adapter._structured_llm = lambda model: _Llm()  # type: ignore[method-assign]

    chips = await adapter.generate_follow_ups(
        model="gpt-test",
        locale="en",
        intent="next_meal",
        slot="lunch",
        user_message="lunch?",
        assistant_text="Try this.",
        has_suggestions=True,
    )

    assert [chip["action"] for chip in chips] == ["remaining_budget", "limits"]


@pytest.mark.asyncio
async def test_generate_follow_ups_returns_empty_on_provider_error() -> None:
    adapter = OpenAIChatCompletionAdapter(api_key="test", timeout_seconds=8)
    structured = AsyncMock()
    structured.ainvoke.side_effect = RuntimeError("pydantic serialize")

    class _Llm:
        def with_structured_output(self, schema, **kwargs):
            del schema, kwargs
            return structured

    adapter._structured_llm = lambda model: _Llm()  # type: ignore[method-assign]

    chips = await adapter.generate_follow_ups(
        model="gpt-test",
        locale="en",
        intent="next_meal",
        slot="lunch",
        user_message="lunch?",
        assistant_text="Try this.",
        has_suggestions=True,
    )

    assert chips == []
