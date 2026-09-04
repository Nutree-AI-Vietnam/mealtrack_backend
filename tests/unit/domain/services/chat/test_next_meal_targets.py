from src.domain.services.chat.next_meal_targets import next_meal_discover_targets


def test_lunch_uses_slot_share_not_remaining_day() -> None:
    targets = next_meal_discover_targets(
        meal_slot="lunch",
        remaining_calories=1932,
        remaining_protein_g=119,
        remaining_carbs_g=222,
        remaining_fat_g=63,
        daily_target_calories=1932,
    )

    assert targets.calorie_target == 724
    assert targets.calorie_target != 1932
    assert targets.protein_target == 44.6
    assert targets.carbs_target == 83.2
    assert targets.fat_target == 23.6


def test_breakfast_uses_slot_share_of_daily_target() -> None:
    targets = next_meal_discover_targets(
        meal_slot="breakfast",
        remaining_calories=650,
        remaining_protein_g=50,
        remaining_carbs_g=80,
        remaining_fat_g=20,
        daily_target_calories=1800,
    )

    assert targets.calorie_target == 450
    assert targets.protein_target == 34.6
    assert targets.carbs_target == 55.4
    assert targets.fat_target == 13.8


def test_remaining_budget_caps_slot_target() -> None:
    targets = next_meal_discover_targets(
        meal_slot="lunch",
        remaining_calories=200,
        remaining_protein_g=40,
        remaining_carbs_g=30,
        remaining_fat_g=10,
        daily_target_calories=1932,
    )

    assert targets.calorie_target == 200
    assert targets.protein_target == 40.0
    assert targets.carbs_target == 30.0
    assert targets.fat_target == 10.0


def test_snack_is_ten_percent_of_daily() -> None:
    targets = next_meal_discover_targets(
        meal_slot="snack",
        remaining_calories=1932,
        remaining_protein_g=119,
        remaining_carbs_g=222,
        remaining_fat_g=63,
        daily_target_calories=1932,
    )

    assert targets.calorie_target == 193
    assert targets.protein_target == 11.9


def test_missing_remaining_falls_back_to_daily_slot() -> None:
    targets = next_meal_discover_targets(
        meal_slot="dinner",
        remaining_calories=None,
        daily_target_calories=2000,
    )

    assert targets.calorie_target == 750
    assert targets.protein_target is None


def test_zero_remaining_skips_targets() -> None:
    targets = next_meal_discover_targets(
        meal_slot="lunch",
        remaining_calories=0,
        remaining_protein_g=0,
        remaining_carbs_g=0,
        remaining_fat_g=0,
        daily_target_calories=1932,
    )

    assert targets.calorie_target is None
    assert targets.protein_target is None
