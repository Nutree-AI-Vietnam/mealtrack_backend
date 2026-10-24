"""Recap stories for weeks the design samples don't cover, in English."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from tests.fixtures.weekly_recap_samples import sample_stats

from src.domain.model.weekly_recap import MissedMeal, WeekStats
from src.domain.services.weekly_recap import RecapConfig, generate_recap

ZEROS = (0.0,) * 7


def _kinds(section: dict[str, Any], key: str = "cards") -> list[str]:
    return [item["kind"] for item in section[key]]


def _card(section: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in section["cards"] if item["kind"] == kind)


def _reminder(stats: WeekStats) -> dict[str, Any]:
    moves = generate_recap(stats)["plan"]["moves"]
    return next(move for move in moves if move["icon"] == "notifications_active")


def test_steady_week_in_english() -> None:
    payload = generate_recap(sample_stats("steady"))

    assert payload["range"] == "Oct 2 – Oct 8"
    assert payload["week_start"] == "2023-10-02"
    assert payload["intro"]["title"] == "Your week, wrapped."
    assert payload["intro"]["chip"]["text"] == "7-day streak, your best yet"
    assert payload["wins"]["title"] == "3 highlights this week."
    assert payload["share"]["foot"] == "Next week: more veggies, fewer late snacks."


def test_story_chips_in_english() -> None:
    bulk = generate_recap(sample_stats("bulk"))["intro"]["chip"]
    comeback = generate_recap(sample_stats("comeback"))["intro"]["chip"]

    assert bulk["text"] == "4 strength sessions, your most ever"
    assert comeback["text"] == "4-day streak and counting"


def test_unknown_locale_renders_english() -> None:
    stats = sample_stats("steady")

    assert generate_recap(stats, locale="pt") == generate_recap(stats, locale="en")


def test_partial_week_counts_only_days_the_account_existed() -> None:
    stats = replace(
        sample_stats("first_week"),
        week_days=5,
        account_days=5,
        meals_by_day=(0, 0, 3, 3, 2, 3, 3),
        kcal_by_day=(0, 0, 1700, 1800, 1650, 1900, 1750),
        protein_by_day=(0, 0, 90, 95, 88, 92, 94),
        water_by_day=(0, 0, 2.0, 1.8, 2.2, 2.4, 2.0),
    )

    intro = generate_recap(stats)["intro"]

    assert (intro["big"], intro["big_unit"]) == (14, "/ 15 meals logged")


def test_low_data_bar_scales_with_the_week() -> None:
    full = generate_recap(sample_stats("low_data"))
    short = generate_recap(
        replace(
            sample_stats("low_data"),
            week_days=3,
            account_days=3,
            meals_by_day=(0, 0, 0, 0, 1, 2, 1),
        )
    )

    assert full["intro"]["chip"]["text"] == "Log 10+ meals to get a full recap"
    assert short["intro"]["chip"]["text"] == "Log 5+ meals to get a full recap"
    assert short["intro"]["big_unit"] == "/ 9 meals logged"


def test_empty_week_has_only_intro_and_plan() -> None:
    stats = replace(
        sample_stats("steady"),
        meals_by_day=(0,) * 7,
        kcal_by_day=ZEROS,
        protein_by_day=ZEROS,
        water_by_day=ZEROS,
        fiber_avg=0,
        streak=0,
        workouts=(),
        late_snack_days=(),
        top_dishes=(),
        unique_dishes=0,
        common_log_hour=None,
        common_meal=None,
        lunch_no_veg_days=0,
        sugary_drinks=0,
    )

    payload = generate_recap(stats)

    assert payload["case_id"] == "low_data"
    assert payload["slides"] == ["intro", "plan"]
    assert payload["intro"]["big"] == 0
    assert len(payload["plan"]["moves"]) == 1


def test_zero_targets_still_render() -> None:
    stats = replace(
        sample_stats("steady"),
        budget=0,
        protein_target=0,
        fiber_target=0,
        water_target=0,
        carbs_target=0,
    )

    payload = generate_recap(stats)

    json.dumps(payload)
    assert {"intro", "plan"} <= set(payload["slides"])


def test_cheat_day_is_not_called_out_as_over_budget() -> None:
    stats = replace(sample_stats("steady"), cheat_days=(5,))

    payload = generate_recap(stats)

    assert generate_recap(sample_stats("steady"))["wins"]["foot"] == (
        "Sat went 240 kcal over, but a workout and a lighter next day balanced it out."
    )
    assert payload["wins"]["foot"] is None
    assert ["6/7", "days on budget"] in payload["share"]["stats"]


def test_day_budget_replaces_the_week_budget() -> None:
    budgets = (1900.0, 1900.0, 1900.0, 1900.0, 1900.0, 2300.0, 1900.0)
    stats = replace(sample_stats("steady"), budget_by_day=budgets)

    payload = generate_recap(stats)

    assert payload["wins"]["foot"] is None
    assert ["7/7", "days on budget"] in payload["share"]["stats"]


def test_kcal_bars_compare_against_the_unrounded_target() -> None:
    # 1396.8 kcal is within 105% of a 1330.4 target (1396.92); the week
    # budget rounds to 1330, whose 105% (1396.5) would draw that bar as over.
    stats = replace(
        sample_stats("perfect"),
        budget=1330,
        budget_by_day=(1330.4,) * 7,
        kcal_by_day=(1290, 1300, 1310, 1280, 1320, 1396.8, 1295),
    )

    viz = _card(generate_recap(stats)["wins"], "calories")["viz"]

    assert viz["target"] == 1396.92
    assert max(viz["vals"]) <= viz["target"]


def test_kcal_bars_carry_each_days_target() -> None:
    budgets = (1900.0, 1900.0, 1900.0, 1900.0, 1900.0, 2300.0, 1900.0)
    stats = replace(sample_stats("steady"), budget_by_day=budgets)

    viz = _card(generate_recap(stats)["wins"], "calories")["viz"]
    week = _card(generate_recap(sample_stats("steady"))["wins"], "calories")["viz"]

    # Each bar turns red past its day's target plus the 5% the count allows.
    assert viz["targets"] == [1995.0] * 5 + [2415.0, 1995.0]
    assert "targets" not in week


def test_kcal_bars_turn_red_where_a_day_stops_counting() -> None:
    on_budget = _card(generate_recap(sample_stats("steady"))["wins"], "calories")
    fueled = _card(generate_recap(sample_stats("bulk"))["wins"], "fat")

    # A day is on budget up to 105% of its 1,900 target.
    assert on_budget["viz"]["target"] == 1995.0
    # Training days still aim for the target itself.
    assert fueled["viz"]["target"] == 2800.0


def test_bulk_day_under_the_floor_is_not_on_budget() -> None:
    # Sat and Sun are under 90% of the 2,800 target.
    payload = generate_recap(sample_stats("bulk"))
    steady = _card(generate_recap(sample_stats("steady"))["wins"], "calories")

    calories = _card(payload["wins"], "calories")

    assert calories["note"] == "5/7 days on budget"
    assert calories["viz"]["floors"] == [2520.0] * 7
    assert ["5/7", "days on budget"] in payload["share"]["stats"]
    assert "floors" not in steady["viz"]


def test_bulk_good_start_needs_both_days_above_the_floor() -> None:
    config = RecapConfig(priorities={"good_start": 100})
    week = (2700, 2750, 2000, 2000, 3200, 2000, 2000)
    good = replace(sample_stats("bulk"), kcal_by_day=week, under_days=(2, 3, 5, 6))
    # Monday's 2,400 kcal is under 90% of the 2,800 target.
    short = replace(good, kcal_by_day=(2400, *week[1:]), under_days=(0, 2, 3, 5, 6))

    start = _card(generate_recap(good, config)["wins"], "calories")

    assert start["title"] == "Strong start to the week"
    assert "calories" not in _kinds(generate_recap(short, config)["wins"])


def test_calories_in_balance_needs_the_week_average_on_budget() -> None:
    # Five days on budget, but two big days put the average 15% over.
    over = replace(
        sample_stats("steady"),
        kcal_by_day=(1900, 1900, 1900, 1900, 1900, 2900, 2900),
    )
    # A bulk week whose average is under 90% of target falls short.
    under = replace(
        sample_stats("bulk"),
        kcal_by_day=(2800, 2800, 2800, 2800, 2800, 800, 800),
        under_days=(5, 6),
    )

    assert "calories" not in _kinds(generate_recap(over)["wins"])
    assert "calories" not in _kinds(generate_recap(under)["wins"])


def test_weekend_card_averages_only_the_days_it_names() -> None:
    # Fri is 100 kcal under; Sat and Sun are 800 and 600 over, so the
    # Fri–Sun average of 433 sets the card off.
    stats = replace(
        sample_stats("tough"),
        kcal_by_day=(1880, 1850, 2150, 2300, 1800, 2700, 2500),
        weekend_delta=433,
    )

    weekend = _card(generate_recap(stats)["grow"], "calories")

    assert weekend["count"] == 700
    assert weekend["note"] == "Over target on Sat & Sun."


def test_fiber_note_names_every_lunch_when_all_lacked_veggies() -> None:
    every = replace(sample_stats("steady"), lunch_no_veg_days=7)

    note = _card(generate_recap(every)["grow"], "fiber")["note"]
    most = _card(generate_recap(sample_stats("steady"))["grow"], "fiber")["note"]

    assert note == "Every lunch was missing veggies."
    assert most == "Most lunches were missing veggies."


def test_reminder_says_what_to_log() -> None:
    moves = generate_recap(sample_stats("low_data"))["plan"]["moves"]

    assert moves[0]["title"] == "Set a reminder to log lunch at 12 PM"
    assert moves[0]["why"] == "When you usually log"


def test_reminder_is_for_the_meal_most_often_forgotten() -> None:
    stats = replace(
        sample_stats("first_week"), missed_meal=MissedMeal("breakfast", 5, 510)
    )
    # Weeks stored before reminder times were kept.
    stored = replace(stats, missed_meal=MissedMeal("breakfast", 5))

    reminder = _reminder(stats)

    assert reminder["title"] == "Set a reminder to log breakfast at 8:30 AM"
    assert reminder["why"] == "The meal you most often forget to log"
    assert _reminder(stored)["title"] == "Set a reminder to log lunch at 12 PM"


def test_forgotten_meal_gets_a_reminder_without_a_usual_hour() -> None:
    stats = replace(
        sample_stats("low_data"),
        common_log_hour=None,
        common_meal=None,
        missed_meal=MissedMeal("dinner", 3, 1080),
    )

    assert _reminder(stats)["title"] == "Set a reminder to log dinner at 6 PM"


def test_priority_override_reorders_what_to_improve() -> None:
    config = RecapConfig(priorities={"water_low": 100})

    payload = generate_recap(sample_stats("steady"), config)

    assert _kinds(payload["grow"]) == ["water", "fiber", "calm"]
    assert _kinds(payload["plan"], "moves")[0] == "water"
    assert payload["share"]["foot"] == "Next week: more water, more veggies."


def test_case_limits_override_card_counts() -> None:
    config = RecapConfig(cases={"steady": {"grow": 1, "advice": 1}})

    payload = generate_recap(sample_stats("steady"), config)

    assert payload["grow"]["title"] == "What's holding you back."
    assert _kinds(payload["grow"]) == ["fiber"]
    assert payload["plan"]["title"] == "Next week, try 1 thing."
    assert _kinds(payload["plan"], "moves") == ["fiber"]


def test_max_cards_limits_wins() -> None:
    payload = generate_recap(sample_stats("steady"), RecapConfig(max_cards=1))

    assert payload["wins"]["title"] == "1 highlight this week."
    assert _kinds(payload["wins"]) == ["protein"]
    assert len(payload["share"]["stats"]) == 3


def test_perfect_week_needs_water_tracking() -> None:
    stats = replace(sample_stats("perfect"), water_by_day=ZEROS)

    payload = generate_recap(stats)

    assert payload["case_id"] == "steady"
    assert "grow" not in payload["slides"]


def test_perfect_week_counts_earlier_perfect_weeks() -> None:
    stats = replace(sample_stats("perfect"), perfect_weeks_before=2)

    chip = generate_recap(stats)["intro"]["chip"]

    assert chip["text"] == "Perfect week #3"


def test_comeback_without_a_streak_has_no_chip_or_wins() -> None:
    stats = replace(sample_stats("comeback"), streak=1)

    payload = generate_recap(stats)

    assert payload["intro"]["chip"] is None
    assert payload["slides"] == ["intro", "plan", "share"]
    assert ["1", "day in a row"] in payload["share"]["stats"]


def test_bulk_week_under_target_is_tough() -> None:
    stats = replace(sample_stats("bulk"), under_days=(0, 2, 5, 6))

    payload = generate_recap(stats)

    assert payload["case_id"] == "tough"
    assert payload["share"] is None


def test_low_data_without_habits_still_gets_a_move() -> None:
    stats = replace(
        sample_stats("low_data"),
        meals_by_day=(1, 1, 1, 0, 1, 1, 0),
        common_log_hour=None,
        common_meal=None,
        missed_meal=None,
        photo_share=1.0,
        streak=0,
        gap_before=0,
        water_by_day=(1.0,) * 7,
    )

    moves = generate_recap(stats)["plan"]["moves"]

    assert [move["title"] for move in moves] == ["Add 1 more workout"]


def test_share_progress_falls_back_to_the_streak() -> None:
    stats = replace(sample_stats("steady"), progress_pct=None, progress_delta=None)

    progress = generate_recap(stats)["share"]["progress"]

    assert progress == {"label": "Current streak", "pct": 0.23, "sub": "7 days"}


def test_share_progress_is_dropped_without_goal_or_streak() -> None:
    stats = replace(
        sample_stats("steady"),
        progress_pct=None,
        streak=0,
        meals_by_day=(3, 3, 2, 3, 3, 2, 0),
    )

    share = generate_recap(stats)["share"]

    assert share is not None
    assert share["progress"] is None
