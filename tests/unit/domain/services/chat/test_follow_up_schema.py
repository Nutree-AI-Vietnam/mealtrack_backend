from src.domain.services.chat.follow_up_schema import (
    ChatFollowUpItem,
    ChatFollowUpList,
    ensure_follow_ups,
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


def test_sanitize_keeps_specific_questions_and_drops_vague_ones() -> None:
    cleaned = sanitize_follow_ups(
        {
            "follow_ups": [
                {"label": "Make the tofu higher protein", "action": "ask"},
                {"label": "Tell me more", "action": "ask"},
                {"label": "I logged it", "action": "ask"},
                {"label": "Only 400 kcal left", "action": "ask"},
                {"label": "Skip the rice", "action": "ask"},
                {"label": "Save this", "action": "save_meal"},
            ]
        }
    )
    assert cleaned == [
        {"label": "Make the tofu higher protein", "action": "ask"},
        {"label": "Skip the rice", "action": "ask"},
    ]


def test_meal_names_keeps_three_dish_names() -> None:
    from src.domain.services.chat.follow_up_schema import meal_names

    assert meal_names(
        [
            {"name": "Tofu bowl"},
            {"name": "  "},
            {"name": "Egg rice"},
            {"name": "Soup"},
            {"name": "Extra"},
        ]
    ) == ["Tofu bowl", "Egg rice", "Soup"]


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


def test_sanitize_maps_function_name_to_chip_alias() -> None:
    cleaned = sanitize_follow_ups(
        {
            "follow_ups": [
                {"label": "More ideas", "action": "suggest_next_meal"},
                {"label": "What's left?", "action": "check_daily_progress"},
            ]
        }
    )
    assert cleaned == [
        {"label": "More ideas", "action": "next_meal"},
        {"label": "What's left?", "action": "remaining_budget"},
    ]


def test_ensure_follow_ups_keeps_two_model_chips() -> None:
    chips = [
        {"label": "What's left?", "action": "remaining_budget"},
        {"label": "More breakfast ideas", "action": "next_meal"},
    ]
    assert (
        ensure_follow_ups(
            chips,
            locale="en",
            intent="next_meal",
            slot="breakfast",
            has_suggestions=True,
        )
        == chips
    )


def test_failed_meal_follow_ups_name_the_featured_dish() -> None:
    chips = ensure_follow_ups(
        [],
        locale="en",
        intent="next_meal",
        slot="lunch",
        has_suggestions=True,
        suggestion_names=["Egg rice bowl", "Yogurt cup"],
    )
    assert chips[0] == {
        "label": "Make Egg rice bowl higher protein",
        "action": "ask",
    }
    assert chips[1]["action"] == "next_meal"

    vietnamese = ensure_follow_ups(
        [],
        locale="vi",
        intent="next_meal",
        slot="lunch",
        has_suggestions=True,
        suggestion_names=["Cơm cá hồi"],
    )
    assert vietnamese[0]["label"] == "Làm Cơm cá hồi nhiều đạm hơn"
    assert vietnamese[0]["action"] == "ask"


def test_ensure_follow_ups_fills_a_failed_generation() -> None:
    chips = ensure_follow_ups(
        [],
        locale="vi",
        intent="day_progress",
        slot="dinner",
        has_suggestions=False,
    )
    assert [chip["action"] for chip in chips] == ["remaining_budget", "next_meal"]
    assert chips[1]["label"] == "Thêm ý tưởng bữa tối"
