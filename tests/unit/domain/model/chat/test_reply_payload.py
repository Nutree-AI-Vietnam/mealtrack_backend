from src.domain.model.chat import ChatMessage, ChatMessageRole, ChatMessageStatus
from src.domain.model.chat.models import empty_reply_payload, reply_sidecar
from src.domain.utils.timezone_utils import utc_now


def _message(payload=None) -> ChatMessage:
    now = utc_now()
    return ChatMessage(
        id="m1",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        reply_payload=payload,
    )


def test_missing_payload_hydrates_as_empty_lists() -> None:
    message = _message(None)
    assert message.suggestions() == []
    assert message.follow_ups() == []
    assert reply_sidecar(message) == empty_reply_payload()


def test_sidecar_includes_discover_session_id() -> None:
    message = _message(
        {
            "suggestions": [{"name": "Oats", "calories": 300}],
            "follow_ups": [],
            "discover_session_id": "sess-9",
        }
    )
    sidecar = reply_sidecar(message)
    assert sidecar["discover_session_id"] == "sess-9"
    assert sidecar["suggestions"][0]["name"] == "Oats"


def test_sidecar_includes_intent() -> None:
    message = _message(
        {
            "suggestions": [],
            "follow_ups": [],
            "intent": "remaining_budget",
        }
    )
    assert message.intent() == "remaining_budget"
    assert reply_sidecar(message)["intent"] == "remaining_budget"


def test_malformed_payload_is_ignored() -> None:
    message = _message({"suggestions": "nope", "follow_ups": [1, {"label": "More"}]})
    assert message.suggestions() == []
    assert message.follow_ups() == [{"label": "More"}]
