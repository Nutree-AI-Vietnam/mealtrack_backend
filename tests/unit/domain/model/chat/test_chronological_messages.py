from src.domain.model.chat import (
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    chronological_chat_messages,
)
from src.domain.utils.timezone_utils import utc_now


def _msg(*, id: str, role: ChatMessageRole, created_at) -> ChatMessage:
    return ChatMessage(
        id=id,
        thread_id="t1",
        role=role,
        status=ChatMessageStatus.COMPLETED,
        created_at=created_at,
        updated_at=created_at,
        content=id,
    )


def test_chronological_chat_messages_puts_user_before_assistant_on_tie():
    now = utc_now()
    assistant = _msg(
        id="zzzz-assistant",
        role=ChatMessageRole.ASSISTANT,
        created_at=now,
    )
    user = _msg(
        id="aaaa-user",
        role=ChatMessageRole.USER,
        created_at=now,
    )

    ordered = chronological_chat_messages([assistant, user])

    assert [m.role for m in ordered] == [
        ChatMessageRole.USER,
        ChatMessageRole.ASSISTANT,
    ]
