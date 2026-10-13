from datetime import UTC, datetime

from src.domain.model.chat import (
    CHAT_HISTORY_LIMIT,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
)
from src.domain.services.chat.memory import earlier_conversation, rolling_memory


def _message(index: int, *, role: ChatMessageRole, content: str) -> ChatMessage:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    return ChatMessage(
        id=f"m{index}",
        thread_id="t1",
        role=role,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content=content,
    )


def test_earlier_conversation_keeps_turns_outside_the_live_window() -> None:
    messages = []
    for index in range(CHAT_HISTORY_LIMIT + 2):
        role = ChatMessageRole.USER if index % 2 == 0 else ChatMessageRole.ASSISTANT
        messages.append(_message(index, role=role, content=f"note {index}"))

    memory = earlier_conversation(messages)

    assert memory is not None
    assert "note 0" in memory
    assert "note 1" in memory
    assert f"note {CHAT_HISTORY_LIMIT}" not in memory
    assert memory.startswith("User: note 0")


def test_earlier_conversation_is_absent_inside_the_live_window() -> None:
    messages = [
        _message(index, role=ChatMessageRole.USER, content=f"note {index}")
        for index in range(CHAT_HISTORY_LIMIT)
    ]
    assert earlier_conversation(messages) is None


def test_earlier_conversation_drops_the_oldest_lines_when_the_cap_is_hit() -> None:
    messages = [
        _message(index, role=ChatMessageRole.USER, content="preference " + ("x" * 200))
        for index in range(CHAT_HISTORY_LIMIT + 30)
    ]
    memory = earlier_conversation(messages)
    assert memory is not None
    assert len(memory) <= 1400
    assert "…" in memory
    assert "preference " + ("x" * 200) not in memory


def test_earlier_conversation_names_a_card_without_its_calories() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    older = [
        ChatMessage(
            id="card",
            thread_id="t1",
            role=ChatMessageRole.ASSISTANT,
            status=ChatMessageStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            content="",
            reply_payload={
                "suggestions": [{"name": "Salmon rice", "calories": 410}],
            },
        )
    ]
    older.extend(
        ChatMessage(
            id=f"old-{index}",
            thread_id="t1",
            role=ChatMessageRole.USER,
            status=ChatMessageStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            content=f"ask {index}",
        )
        for index in range(CHAT_HISTORY_LIMIT)
    )
    memory = earlier_conversation(older)
    assert memory is not None
    assert "Salmon rice" in memory
    assert "410" not in memory


def test_rolling_memory_keeps_a_saved_summary_and_only_new_overflow() -> None:
    older = [
        _message(index, role=ChatMessageRole.USER, content=f"note {index}")
        for index in range(CHAT_HISTORY_LIMIT + 2)
    ]
    first = rolling_memory(older)
    assert first.through_message_id == older[1].id
    assert first.text is not None
    assert "note 0" in first.text

    extended = [
        *older,
        _message(CHAT_HISTORY_LIMIT + 2, role=ChatMessageRole.USER, content="note new"),
        _message(
            CHAT_HISTORY_LIMIT + 3,
            role=ChatMessageRole.ASSISTANT,
            content="noted",
        ),
    ]
    second = rolling_memory(
        extended,
        stored_summary=first.text,
        summary_through_message_id=first.through_message_id,
    )
    assert second.text is not None
    assert "note 0" in second.text
    assert "note 2" in second.text
    assert "note 3" in second.text
    assert "note new" not in second.text
    assert second.through_message_id == extended[3].id


def test_rolling_memory_returns_the_saved_summary_inside_the_live_window() -> None:
    messages = [
        _message(index, role=ChatMessageRole.USER, content=f"note {index}")
        for index in range(3)
    ]
    memory = rolling_memory(messages, stored_summary="User: no cilantro")
    assert memory.text == "User: no cilantro"
    assert memory.through_message_id is None
