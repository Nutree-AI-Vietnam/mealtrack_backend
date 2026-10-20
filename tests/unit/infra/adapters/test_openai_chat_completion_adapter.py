from unittest.mock import AsyncMock

import pytest

from src.infra.adapters.openai_chat_completion_adapter import (
    OpenAIChatCompletionAdapter,
    _response_truncated,
    _structured_payload,
)


def test_response_truncated_detects_reasoning_budget_cutoff() -> None:
    class _Chunk:
        def __init__(self, metadata):
            self.response_metadata = metadata

    assert _response_truncated(_Chunk({"finish_reason": "length"})) is True
    assert (
        _response_truncated(
            _Chunk(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                }
            )
        )
        is True
    )
    assert _response_truncated(_Chunk({"finish_reason": "stop"})) is False
    assert (
        _response_truncated(
            _Chunk(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "content_filter"},
                }
            )
        )
        is False
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


class _ClosableLangChainStream:
    def __init__(self) -> None:
        self.closed = False
        self._done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True

        class _Chunk:
            content = "Hello"

        return _Chunk()

    async def aclose(self) -> None:
        self.closed = True


class _FakeStreamingLlm:
    def __init__(self, stream: _ClosableLangChainStream) -> None:
        self.stream = stream

    def astream(self, messages, **kwargs):
        del messages, kwargs
        return self.stream


@pytest.mark.asyncio
async def test_stream_aclose_closes_the_langchain_stream() -> None:
    adapter = OpenAIChatCompletionAdapter(api_key="test", timeout_seconds=8)
    inner = _ClosableLangChainStream()
    adapter._llm = lambda model: _FakeStreamingLlm(inner)  # type: ignore[method-assign]
    generated = adapter.stream(
        model="gpt-test",
        system_instructions="sys",
        grounding_message="ground",
        history=[],
        user_message="hello",
        max_output_tokens=16,
    )
    delta = await anext(generated)
    assert delta.text == "Hello"
    assert inner.closed is False

    await generated.aclose()

    assert inner.closed is True


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
