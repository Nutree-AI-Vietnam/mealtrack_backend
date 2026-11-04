from datetime import UTC, datetime

from src.domain.services.chat.rewind import is_latest_message, message_ids_after


def test_message_ids_after_drops_only_later_turns() -> None:
    created = datetime(2026, 9, 1, tzinfo=UTC)
    later = datetime(2026, 9, 2, tzinfo=UTC)
    ids = message_ids_after(
        "assistant-1",
        created,
        "assistant",
        [
            ("user-1", created, "user"),
            ("assistant-1", created, "assistant"),
            ("user-2", later, "user"),
            ("assistant-2", later, "assistant"),
        ],
    )
    assert ids == ["user-2", "assistant-2"]


def test_a_reply_with_a_newer_turn_is_not_the_latest() -> None:
    created = datetime(2026, 9, 1, tzinfo=UTC)
    later = datetime(2026, 9, 2, tzinfo=UTC)
    messages = [
        ("user-1", created, "user"),
        ("assistant-1", created, "assistant"),
        ("user-2", later, "user"),
    ]
    assert is_latest_message("assistant-1", created, "assistant", messages) is False
    assert is_latest_message("user-2", later, "user", messages) is True


def test_message_ids_after_is_empty_for_the_latest_reply() -> None:
    created = datetime(2026, 9, 2, tzinfo=UTC)
    assert (
        message_ids_after(
            "assistant-2",
            created,
            "assistant",
            [
                ("user-2", created, "user"),
                ("assistant-2", created, "assistant"),
            ],
        )
        == []
    )
