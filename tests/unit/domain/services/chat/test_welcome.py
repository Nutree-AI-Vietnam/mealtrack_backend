from src.domain.model.chat import ChatMealSummary, ChatUserContext
from src.domain.services.chat.welcome import coach_welcome


def _context(**overrides) -> ChatUserContext:
    values = {
        "context_version": "chat_context_v1",
        "as_of": "2026-09-01T00:00:00+00:00",
        "locale": "en",
        "timezone": "UTC",
        "allergies": [],
        "health_conditions": [],
        "dietary_preferences": [],
        "goal": None,
        "tdee": None,
        "target_calories": 1800,
        "target_protein_g": 140,
        "target_carbs_g": 180,
        "target_fat_g": 60,
        "consumed_calories": 1150,
        "consumed_protein_g": 90,
        "consumed_carbs_g": 100,
        "consumed_fat_g": 40,
        "remaining_calories": 650.4,
        "remaining_protein_g": 50.2,
        "remaining_carbs_g": 79.6,
        "remaining_fat_g": 20,
        "remaining_days": 4,
        "local_hour": 19,
        "suggested_meal_slot": "dinner",
        "local_date": "2026-09-01",
        "preferred_name": " Alex ",
        "recent_meals": (
            ChatMealSummary(
                meal_id="m1",
                local_date="2026-08-31",
                dish_name="Old soup",
                meal_type="dinner",
                calories=300,
                protein_g=10,
                carbs_g=20,
                fat_g=8,
                status="ready",
            ),
            ChatMealSummary(
                meal_id="m2",
                local_date="2026-09-01",
                dish_name="Egg rice",
                meal_type="breakfast",
                calories=420,
                protein_g=28,
                carbs_g=45,
                fat_g=12,
                status="ready",
            ),
        ),
    }
    values.update(overrides)
    return ChatUserContext(**values)


def test_welcome_uses_authoritative_numbers_name_and_todays_meal() -> None:
    welcome = coach_welcome(_context())
    assert welcome == {
        "preferred_name": "Alex",
        "remaining_calories": 650,
        "remaining_protein_g": 50,
        "remaining_carbs_g": 80,
        "suggested_meal_slot": "dinner",
        "latest_meal_name": "Egg rice",
    }


def test_welcome_drops_unknown_slot_and_oversized_labels() -> None:
    welcome = coach_welcome(
        _context(
            preferred_name="A" * 41,
            suggested_meal_slot="brunch",
            recent_meals=(),
            remaining_calories=None,
        )
    )
    assert welcome["preferred_name"] is None
    assert welcome["suggested_meal_slot"] is None
    assert welcome["latest_meal_name"] is None
    assert welcome["remaining_calories"] is None


def test_welcome_keeps_a_negative_remaining_budget() -> None:
    welcome = coach_welcome(_context(remaining_calories=-40.2))
    assert welcome["remaining_calories"] == -40
