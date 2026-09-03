from src.domain.services.chat.follow_up_schema import (
    ChatFollowUpItem,
    ChatFollowUpList,
    sanitize_follow_ups,
)


def test_sanitize_keeps_valid_unique_actions() -> None:
    raw = ChatFollowUpList(
        follow_ups=[
            ChatFollowUpItem(label="What's left?", action="remaining_budget"),
            ChatFollowUpItem(label="More ideas", action="next_meal"),
            ChatFollowUpItem(label="How is today?", action="day_progress"),
            ChatFollowUpItem(label="Extra", action="limits"),
        ]
    )
    cleaned = sanitize_follow_ups(raw)
    assert [item["action"] for item in cleaned] == [
        "remaining_budget",
        "next_meal",
        "day_progress",
    ]


def test_sanitize_drops_unknown_and_blank() -> None:
    cleaned = sanitize_follow_ups(
        {
            "follow_ups": [
                {"label": "Save this", "action": "save_meal"},
                {"label": "", "action": "next_meal"},
                {"label": "What can you do?", "action": "limits"},
            ]
        }
    )
    assert cleaned == [{"label": "What can you do?", "action": "limits"}]
