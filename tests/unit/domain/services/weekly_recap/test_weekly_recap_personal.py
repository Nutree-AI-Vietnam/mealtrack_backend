"""What makes a recap the user's own: their name, foods, diet and last week."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from tests.fixtures.weekly_recap_samples import SAMPLES, sample_stats

from src.domain.model.weekly_recap import (
    LastWeek,
    PeakMeal,
    WeekStats,
    week_stats_from_dict,
    week_stats_to_dict,
)
from src.domain.services.weekly_recap import RecapConfig, generate_recap
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.facts import week_facts
from src.domain.services.weekly_recap.insights_good import GOOD
from src.domain.services.weekly_recap.last_week import (
    last_week_of,
    planned_issue_ids,
    shown_plan,
)
from src.domain.services.weekly_recap.strings import TABLES

CONFIG = RecapConfig()


def _cards(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    return payload[section]["cards"] if payload[section] else []


def _card(payload: dict[str, Any], section: str, kind: str) -> dict[str, Any]:
    return next(card for card in _cards(payload, section) if card["kind"] == kind)


def _last_week(**values: Any) -> LastWeek:
    """The steady sample week as the week before, with ``values`` changed."""
    base = last_week_of(sample_stats("steady"), "steady", CONFIG)
    return replace(base, **values)


def _protein_low_week(**values: Any) -> WeekStats:
    """A steady week short on protein every day."""
    return replace(sample_stats("steady"), protein_by_day=(60.0,) * 7, **values)


PROTEIN_FIRST = RecapConfig(priorities={"protein_low": 99})


# --- The user's name -------------------------------------------------------


@pytest.mark.parametrize("locale", sorted(TABLES))
@pytest.mark.parametrize("name", list(SAMPLES))
def test_the_title_greets_the_user_by_name(name: str, locale: str) -> None:
    stats = replace(sample_stats(name), first_name="Linh")

    intro = generate_recap(stats, locale=locale)["intro"]

    assert "Linh" in intro["title"]
    assert "{" not in intro["title"]


def test_the_named_title_keeps_the_story() -> None:
    steady = replace(sample_stats("steady"), first_name="Linh")
    tough = replace(sample_stats("tough"), first_name="Linh")

    assert generate_recap(steady)["intro"]["title"] == "Linh, your week, wrapped."
    assert generate_recap(steady, locale="vi")["intro"]["title"] == (
        "Tuần của Linh, gói gọn."
    )
    assert generate_recap(tough)["intro"]["title"] == (
        "A tough week, Linh. That's okay."
    )


def test_the_name_stays_off_the_share_card() -> None:
    stats = replace(sample_stats("steady"), first_name="Linh")

    share = generate_recap(stats)["share"]

    assert "Linh" not in str(share)


# --- Foods the user actually ate -------------------------------------------


def test_a_full_week_names_the_go_to_dish() -> None:
    sub = generate_recap(sample_stats("steady"))["intro"]["sub"]

    assert sub == "Phở bò was your go-to, 3 times. Here's the rest of your week."


def test_a_dish_logged_twice_is_not_a_go_to() -> None:
    stats = sample_stats("steady")
    twice = replace(stats, top_dishes=(replace(stats.top_dishes[0], count=2),))

    sub = generate_recap(twice)["intro"]["sub"]

    assert sub == "What went well, what didn't, and what to try next."


def test_a_tough_week_keeps_its_gentle_line() -> None:
    sub = generate_recap(sample_stats("tough"))["intro"]["sub"]

    assert sub.startswith("Everyone has weeks like this.")


def test_the_water_note_names_the_driest_day() -> None:
    water = _card(generate_recap(sample_stats("steady")), "grow", "water")

    assert water["note"] == "Hit 2/7 days, lowest on Sat."


def test_the_driest_day_skips_days_before_sign_up() -> None:
    stats = replace(
        sample_stats("steady"),
        week_days=5,
        water_by_day=(0.0, 0.0, 2.0, 1.2, 1.9, 1.6, 2.1),
    )

    water = _card(generate_recap(stats), "grow", "water")

    assert water["note"].endswith("lowest on Thu.")


def test_the_weekend_note_names_the_biggest_meal_on_an_over_day() -> None:
    # Only Sat and Sun are over target; Friday's bigger meal doesn't count.
    stats = replace(
        sample_stats("tough"),
        kcal_by_day=(1880, 1850, 2150, 2300, 1800, 2700, 2500),
        weekend_delta=433,
        weekend_peaks=(
            PeakMeal(4, "Bún bò", 2000),
            PeakMeal(5, "Lẩu Thái", 1450),
            PeakMeal(6, "Pizza", 1600),
        ),
    )

    weekend = _card(generate_recap(stats), "grow", "calories")

    assert weekend["note"] == "Sat & Sun. Biggest meal: Pizza, 1,600 kcal."


def test_the_sugary_card_names_the_drink() -> None:
    stats = replace(
        sample_stats("steady"),
        sugary_drinks=5,
        sugary_kcal=1100,
        top_sugary_drink="Trà sữa trân châu",
    )
    config = RecapConfig(priorities={"sugary": 99})

    payload = generate_recap(stats, config)

    sugary = _card(payload, "grow", "fat")
    assert sugary["note"] == "Most often: Trà sữa trân châu, about 1,100 kcal in all."
    assert payload["plan"]["moves"][0]["title"] == (
        "Trà sữa trân châu → unsweetened tea"
    )
    vi = generate_recap(stats, config, "vi")["plan"]["moves"][0]
    assert vi["title"] == "Đổi Trà sữa trân châu sang trà không đường"


def test_the_sugary_card_without_a_main_drink_keeps_the_generic_copy() -> None:
    stats = replace(sample_stats("steady"), sugary_drinks=5, sugary_kcal=1100)

    payload = generate_recap(stats, RecapConfig(priorities={"sugary": 99}))

    assert payload["plan"]["moves"][0]["title"] == "Milk tea → unsweetened tea"


@pytest.mark.parametrize(
    ("diet", "title"),
    [
        (None, "Add eggs or tofu to lunch"),
        ("vegetarian", "Add eggs or Greek yogurt to lunch"),
        ("vegan", "Add tofu or beans to lunch"),
        ("keto", "Add eggs or fish to lunch"),
    ],
)
def test_protein_advice_fits_the_diet_and_the_weakest_meal(diet, title) -> None:
    stats = _protein_low_week(diet=diet, low_protein_meal="lunch")

    [move, *_] = generate_recap(stats, PROTEIN_FIRST)["plan"]["moves"]

    assert move["title"] == title
    assert move["why"] == "About 50g short a day, lowest at lunch"


def test_protein_advice_without_a_weakest_meal_suggests_breakfast() -> None:
    [move, *_] = generate_recap(_protein_low_week(), PROTEIN_FIRST)["plan"]["moves"]

    assert move["title"] == "Add eggs or tofu to breakfast"
    assert move["why"] == "About 50g short each day"


@pytest.mark.parametrize(
    ("locale", "title"),
    [
        ("vi", "Thêm đậu phụ hoặc các loại đậu vào bữa tối"),
        ("es", "Añade tofu o legumbres a tu cena"),
        ("de", "Tofu oder Hülsenfrüchte zum Abendessen"),
        ("ja", "夕食に豆腐か豆類をプラス"),
    ],
)
def test_protein_advice_reads_in_every_language(locale: str, title: str) -> None:
    stats = _protein_low_week(diet="vegan", low_protein_meal="dinner")

    move = generate_recap(stats, PROTEIN_FIRST, locale)["plan"]["moves"][0]

    assert move["title"] == title


def _fiber_hit_card(stats: WeekStats) -> dict[str, Any]:
    ctx = RecapContext(stats, week_facts(stats, CONFIG), CONFIG, "en")
    fiber_hit = next(insight for insight in GOOD if insight.id == "fiber_hit")
    return fiber_hit.card(ctx)


def test_the_fiber_win_counts_the_days_it_was_hit() -> None:
    stats = replace(
        sample_stats("perfect"),
        fiber_avg=27.0,
        fiber_by_day=(30.0, 26.0, 24.0, 28.0, 22.0, 31.0, 25.0),  # target 25
    )

    assert _fiber_hit_card(stats)["note"] == "Hit your target 5/7 days"


def test_the_fiber_win_of_an_old_snapshot_makes_no_claim_about_meals() -> None:
    stats = replace(sample_stats("perfect"), fiber_avg=27.0)

    assert _fiber_hit_card(stats)["note"] == "A solid daily average."


# --- This week against last week -------------------------------------------


@pytest.mark.parametrize("name", list(SAMPLES))
def test_no_trend_without_last_week(name: str) -> None:
    payload = generate_recap(sample_stats(name))

    for section in ("wins", "grow"):
        assert all("trend" not in card for card in _cards(payload, section))


def test_a_win_shows_how_far_it_came() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(protein_avg=98.0))

    protein = _card(generate_recap(stats), "wins", "protein")

    assert protein["trend"] == {
        "text": "Up from 98g last week",
        "up": True,
        "good": True,
    }


def test_a_win_does_not_point_out_a_slip() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(protein_avg=130.0))

    protein = _card(generate_recap(stats), "wins", "protein")

    assert "trend" not in protein


def test_an_issue_shows_progress_and_setbacks() -> None:
    fewer = replace(sample_stats("steady"), last_week=_last_week(late_snack_nights=6))
    more = replace(sample_stats("steady"), last_week=_last_week(late_snack_nights=3))

    better = _card(generate_recap(fewer), "grow", "calm")["trend"]
    worse = _card(generate_recap(more), "grow", "calm")["trend"]

    assert better == {"text": "Down from 6 last week", "up": False, "good": True}
    assert worse == {"text": "Up from 3 last week", "up": True, "good": False}


def test_a_change_smaller_than_a_step_is_not_a_trend() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(protein_avg=110.0))

    assert "trend" not in _card(generate_recap(stats), "wins", "protein")


def test_counts_only_compare_the_same_number_of_days_logged() -> None:
    # Last week logged five days: its days on budget and late-snack nights
    # don't compare with a seven-day week, its averages do.
    last = _last_week(
        days_logged=5, timed_days=5, days_on_budget=2, late_snack_nights=1
    )
    stats = replace(sample_stats("steady"), last_week=replace(last, fiber_avg=10.0))

    payload = generate_recap(stats)

    assert "trend" not in _card(payload, "wins", "calories")
    assert "trend" not in _card(payload, "grow", "calm")
    assert _card(payload, "grow", "fiber")["trend"]["text"] == "Up from 10g last week"


def test_trends_read_in_every_language() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(protein_avg=98.0))

    for locale in TABLES:
        trend = _card(generate_recap(stats, locale=locale), "wins", "protein")["trend"]
        assert "98" in trend["text"]


# --- Last week's plan ------------------------------------------------------


def test_last_week_remembers_what_its_plan_took_on() -> None:
    steady = last_week_of(sample_stats("steady"), "steady", CONFIG)
    bulk = last_week_of(sample_stats("bulk"), "bulk", CONFIG)

    assert steady.plan == ("fiber_low", "late_snack", "water_low")
    assert bulk.plan == ("rest_under", "preworkout_carbs")
    assert (steady.late_snack_nights, steady.days_on_budget) == (4, 6)
    assert steady.water_avg == pytest.approx(13.8 / 7)


@pytest.mark.parametrize("case_id", ["first_week", "low_data", "perfect", "comeback"])
def test_a_plan_of_habits_or_a_challenge_took_on_no_issue(case_id: str) -> None:
    assert last_week_of(sample_stats(case_id), case_id, CONFIG).plan == ()


def test_an_unknown_story_took_on_no_issue() -> None:
    assert last_week_of(sample_stats("steady"), "retired", CONFIG).plan == ()


def test_last_week_without_water_logged_does_not_compare_water() -> None:
    assert (
        last_week_of(sample_stats("first_week"), "first_week", CONFIG).water_avg is None
    )


def _followed_week(**values: Any) -> WeekStats:
    """Steady, with one late-snack night after last week's plan took on four."""
    return replace(
        sample_stats("steady"),
        late_snack_days=(4,),
        last_week=_last_week(**values),
    )


def test_sticking_to_the_plan_leads_the_wins() -> None:
    payload = generate_recap(_followed_week(plan=("late_snack",)))

    first = payload["wins"]["cards"][0]
    assert first == {
        "kind": "calm",
        "icon": "check_circle",
        "title": "You stuck to last week's plan",
        "value": "1",
        "unit": "late-snack night",
        "note": "Last week's goal: fewer late snacks",
        "trend": {"text": "Down from 4 last week", "up": False, "good": True},
    }
    assert all(card["kind"] != "calm" for card in _cards(payload, "grow"))


def test_the_first_planned_fix_gets_the_card() -> None:
    # Fiber went up 3g but is still low, so the plan's second item, late
    # snacks, is the win; and with fiber still open, the title claims one
    # goal, not the whole plan.
    stats = _followed_week(plan=("fiber_low", "late_snack"), fiber_avg=11.0)

    first = generate_recap(stats)["wins"]["cards"][0]

    assert first["unit"] == "late-snack night"
    assert first["title"] == "You hit one of last week's goals"


def test_an_issue_that_still_holds_is_no_win() -> None:
    # Six late-snack nights down to four is better, but four still counts.
    last = _last_week(late_snack_nights=6, plan=("late_snack",))
    stats = replace(sample_stats("steady"), last_week=last)

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_a_fix_with_nothing_to_compare_is_no_win() -> None:
    # This week's water is fine, but last week logged none, so no "before".
    stats = replace(
        sample_stats("perfect"),
        last_week=_last_week(water_avg=None, plan=("water_low",)),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_the_follow_through_card_reads_in_every_language() -> None:
    stats = _followed_week(plan=("late_snack",))

    for locale in TABLES:
        first = generate_recap(stats, locale=locale)["wins"]["cards"][0]
        assert first["icon"] == "check_circle"
        assert first["value"] == "1"
        assert "4" in first["trend"]["text"]
        assert all("{" not in first[key] for key in ("title", "unit", "note"))


# --- Snapshots -------------------------------------------------------------


def test_the_new_numbers_survive_the_snapshot() -> None:
    stats = replace(
        sample_stats("steady"),
        first_name="Linh",
        diet="vegan",
        fiber_by_day=(20.0,) * 7,
        low_protein_meal="breakfast",
        top_sugary_drink="Trà sữa",
        weekend_peaks=(PeakMeal(5, "Lẩu", 1200),),
        last_week=_last_week(),
    )

    assert week_stats_from_dict(week_stats_to_dict(stats)) == stats


def test_an_old_snapshot_loads_without_the_new_numbers() -> None:
    old = {
        key: value
        for key, value in week_stats_to_dict(sample_stats("steady")).items()
        if key not in {"first_name", "last_week", "weekend_peaks", "fiber_by_day"}
    }

    stats = week_stats_from_dict(old)

    assert (stats.first_name, stats.last_week, stats.weekend_peaks) == (None, None, ())


def test_the_water_note_names_no_day_when_the_lowest_days_tie() -> None:
    stats = replace(sample_stats("steady"), water_by_day=(2.0,) * 7)

    water = _card(generate_recap(stats), "grow", "water")

    assert water["note"] == "Hit 0/7 days."


def test_the_follow_through_unit_agrees_with_the_count() -> None:
    none_left = replace(_followed_week(plan=("late_snack",)), late_snack_days=())

    first = generate_recap(none_left)["wins"]["cards"][0]

    assert (first["value"], first["unit"]) == ("0", "late-snack nights")


# --- Review follow-ups -----------------------------------------------------


def _without_weekend(stats: WeekStats) -> WeekStats:
    """``stats`` with nothing logged Friday to Sunday."""
    return replace(
        stats,
        meals_by_day=(*stats.meals_by_day[:4], 0, 0, 0),
        meal_types_by_day=(*stats.meal_types_by_day[:4], 0, 0, 0),
        kcal_by_day=(*stats.kcal_by_day[:4], 0.0, 0.0, 0.0),
        protein_by_day=(*stats.protein_by_day[:4], 0.0, 0.0, 0.0),
        water_by_day=(*stats.water_by_day[:4], 0.0, 0.0, 0.0),
        weekend_delta=0.0,
        under_days=tuple(day for day in stats.under_days if day < 4),
    )


def _tough_last_week() -> LastWeek:
    """The tough sample as last week: its plan took on fiber and weekends."""
    last = last_week_of(sample_stats("tough"), "tough", CONFIG)
    assert last.plan == ("fiber_low", "weekend_over")
    return last


def test_an_unlogged_weekend_is_not_a_fixed_weekend() -> None:
    stats = replace(
        _without_weekend(sample_stats("steady")), last_week=_tough_last_week()
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_a_weekend_of_cheat_days_is_not_a_fixed_weekend() -> None:
    steady = sample_stats("steady")
    stats = replace(
        steady,
        kcal_by_day=(*steady.kcal_by_day[:5], 3200.0, 3000.0),
        cheat_days=(5, 6),
        last_week=_tough_last_week(),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_a_logged_weekend_back_near_budget_is_a_fixed_weekend() -> None:
    # Steady logs all of Friday to Sunday and only Saturday goes over.
    stats = replace(sample_stats("steady"), last_week=_tough_last_week())

    first = generate_recap(stats)["wins"]["cards"][0]

    assert (first["icon"], first["value"], first["unit"]) == (
        "check_circle",
        "1",
        "weekend day over budget",
    )
    assert first["trend"]["text"] == "Down from 3 last week"


def _thin_weekend(stats: WeekStats, meal_types: int, kcal: float) -> WeekStats:
    """``stats`` with Friday to Sunday logged as two dishes, ``kcal`` a day."""
    return replace(
        stats,
        meals_by_day=(*stats.meals_by_day[:4], 2, 2, 2),
        meal_types_by_day=(*stats.meal_types_by_day[:4], *(meal_types,) * 3),
        kcal_by_day=(*stats.kcal_by_day[:4], kcal, kcal, kcal),
        weekend_delta=kcal - stats.budget,
    )


@pytest.mark.parametrize(
    ("meal_types", "kcal"),
    [(1, 850.0), (2, 1000.0)],
    ids=["lunch as two dishes", "dinner out not logged"],
)
def test_a_thinly_logged_weekend_is_not_a_fixed_weekend(
    meal_types: int, kcal: float
) -> None:
    stats = replace(
        _thin_weekend(sample_stats("steady"), meal_types, kcal),
        last_week=_tough_last_week(),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_averages_compare_only_weeks_logged_as_fully() -> None:
    # Last week only breakfast was logged Thursday to Sunday, or it was a
    # low-data week: its averages are low from logging, not from eating.
    sparse = _last_week(protein_avg=60.0, thin_days=4, plan=("protein_low",))
    low_data = last_week_of(sample_stats("low_data"), "low_data", CONFIG)
    full = _last_week(protein_avg=60.0, plan=("protein_low",))

    def first_win(last: LastWeek) -> dict[str, Any]:
        stats = replace(sample_stats("steady"), last_week=last)
        return generate_recap(stats)["wins"]["cards"][0]

    assert first_win(sparse)["icon"] != "check_circle"
    assert "trend" not in first_win(sparse)
    assert "trend" not in first_win(low_data)
    assert first_win(full)["icon"] == "check_circle"
    assert first_win(full)["trend"]["text"] == "Up from 60g last week"


def test_days_on_budget_compare_only_fully_logged_days() -> None:
    # Only a breakfast logged Friday to Sunday keeps those days under budget.
    steady = sample_stats("steady")
    stats = replace(
        steady,
        meals_by_day=(*steady.meals_by_day[:4], 1, 1, 1),
        meal_types_by_day=(*steady.meal_types_by_day[:4], 1, 1, 1),
        kcal_by_day=(*steady.kcal_by_day[:4], 450.0, 450.0, 450.0),
        weekend_delta=0.0,
        last_week=_last_week(days_on_budget=4),
    )
    control = replace(steady, last_week=_last_week(days_on_budget=4))

    assert "trend" not in _card(generate_recap(stats), "wins", "calories")
    assert _card(generate_recap(control), "wins", "calories")["trend"]["good"]


def test_a_goal_change_is_not_following_the_plan() -> None:
    last = last_week_of(sample_stats("bulk"), "bulk", CONFIG)
    assert last.plan[0] == "rest_under"
    # On a cut no rest day is under-eaten against the new, lower target:
    # the bulk issue stops holding with no change in what was eaten.
    stats = replace(sample_stats("bulk"), goal="cut", under_days=(), last_week=last)

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_counts_against_the_target_need_about_the_same_target() -> None:
    steady = sample_stats("steady")
    moved = replace(_tough_last_week(), budget=round(steady.budget * 1.2))

    payload = generate_recap(replace(steady, last_week=moved))

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_unlogged_rest_days_are_not_fixed_rest_days() -> None:
    last = last_week_of(sample_stats("bulk"), "bulk", CONFIG)
    assert last.plan[0] == "rest_under"
    stats = replace(_without_weekend(sample_stats("bulk")), last_week=last)

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_fewer_logged_days_are_not_fewer_late_snacks() -> None:
    stats = replace(
        _without_weekend(sample_stats("steady")),
        late_snack_days=(1,),
        last_week=_last_week(plan=("late_snack",)),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_water_compares_only_over_the_same_days_with_drinks() -> None:
    fewer = replace(sample_stats("steady"), last_week=_last_week(water_days=5))
    same = replace(sample_stats("steady"), last_week=_last_week(water_avg=1.5))

    assert "trend" not in _card(generate_recap(fewer), "grow", "water")
    assert _card(generate_recap(same), "grow", "water")["trend"]["text"] == (
        "Up from 1.5 L last week"
    )


def test_a_water_change_of_exactly_one_step_is_a_trend() -> None:
    now = sum(sample_stats("steady").water_by_day) / 7
    stats = replace(sample_stats("steady"), last_week=_last_week(water_avg=now - 0.2))

    assert _card(generate_recap(stats), "grow", "water")["trend"]["up"] is True


def test_the_counted_trends_say_what_they_count() -> None:
    stats = replace(
        sample_stats("steady"), last_week=_last_week(days_on_budget=4, workouts=1)
    )

    payload = generate_recap(stats, RecapConfig(max_cards=4))

    calories = _card(payload, "wins", "calories")["trend"]["text"]
    move = _card(payload, "wins", "move")["trend"]["text"]
    assert calories == "Up from 4 days last week"
    assert move == "Up from 1 workout last week"


def test_a_workout_slip_is_not_shown_on_the_moving_win() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(workouts=6))

    payload = generate_recap(stats, RecapConfig(max_cards=4))

    assert "trend" not in _card(payload, "wins", "move")


@pytest.mark.parametrize(
    ("last_drinks", "trend"),
    [
        (7, {"text": "Down from 7 last week", "up": False, "good": True}),
        (3, {"text": "Up from 3 last week", "up": True, "good": False}),
    ],
)
def test_the_sugary_card_shows_progress_and_setbacks(last_drinks, trend) -> None:
    stats = replace(
        sample_stats("steady"),
        sugary_drinks=5,
        sugary_kcal=1100,
        last_week=_last_week(sugary_drinks=last_drinks),
    )

    payload = generate_recap(stats, RecapConfig(priorities={"sugary": 99}))

    assert _card(payload, "grow", "fat")["trend"] == trend


def test_the_protein_and_carb_issues_show_progress() -> None:
    protein = replace(_protein_low_week(), last_week=_last_week(protein_avg=50.0))
    bulk_last = last_week_of(sample_stats("bulk"), "bulk", CONFIG)
    carbs = replace(
        sample_stats("bulk"), last_week=replace(bulk_last, pre_workout_carbs=20.0)
    )

    protein_card = _card(generate_recap(protein, PROTEIN_FIRST), "grow", "protein")
    carbs_card = _card(generate_recap(carbs), "grow", "carbs")

    assert protein_card["trend"]["text"] == "Up from 50g last week"
    assert carbs_card["trend"]["text"] == "Up from 20g last week"


def test_what_nutree_learned_shows_only_progress() -> None:
    def protein_trend(before: float) -> dict[str, Any] | None:
        # The first week has one thin day (Monday); so has the week before.
        stats = replace(
            sample_stats("first_week"),
            last_week=_last_week(protein_avg=before, thin_days=1),
        )
        return _card(generate_recap(stats), "wins", "protein").get("trend")

    assert protein_trend(70.0) == {
        "text": "Up from 70g last week",
        "up": True,
        "good": True,
    }
    assert protein_trend(120.0) is None


def test_the_whole_plan_title_needs_every_item_fixed() -> None:
    stats = replace(
        sample_stats("steady"),
        fiber_avg=24.0,
        late_snack_days=(4,),
        last_week=_last_week(plan=("fiber_low", "late_snack")),
    )

    first = generate_recap(stats)["wins"]["cards"][0]

    assert first["title"] == "You stuck to last week's plan"
    assert first["unit"] == "fiber / day"


def test_a_fix_that_reads_as_a_win_gets_a_share_tile() -> None:
    fiber = replace(
        sample_stats("steady"),
        fiber_avg=24.0,
        last_week=_last_week(plan=("fiber_low",)),
    )
    snacks = _followed_week(plan=("late_snack",))

    fiber_tiles = generate_recap(fiber)["share"]["stats"]
    snack_tiles = generate_recap(snacks)["share"]["stats"]

    assert fiber_tiles[0] == ["24g", "fiber / day"]
    assert all("late-snack" not in label for _, label in snack_tiles)


@pytest.mark.parametrize("locale", sorted(TABLES))
def test_a_protein_follow_through_shares_its_number_once(locale: str) -> None:
    # The wins: the protein follow-through, days on budget and fiber, which
    # has no tile. The protein win the follow-through displaced must not
    # bring the same number back under its own label (ja, zh).
    steady = sample_stats("steady")
    stats = replace(
        steady,
        workouts=(),
        fiber_avg=24.0,
        late_snack_days=(),
        water_by_day=(2.2,) * 7,
        last_week=_last_week(
            protein_avg=80.0, fiber_avg=24.0, late_snack_nights=0, plan=("protein_low",)
        ),
    )

    payload = generate_recap(stats, locale=locale)
    values = [value for value, _ in payload["share"]["stats"]]

    assert payload["wins"]["cards"][0]["icon"] == "check_circle"
    assert len(values) == len(set(values))


def test_the_fiber_win_names_no_days_it_did_not_hit() -> None:
    stats = replace(sample_stats("perfect"), fiber_avg=23.0, fiber_by_day=(23.0,) * 7)

    assert _fiber_hit_card(stats)["note"] == "A solid daily average."


def test_the_driest_day_is_a_day_something_was_logged() -> None:
    steady = sample_stats("steady")
    # Wednesday: no food, no drink. Saturday stays the driest logged day.
    stats = replace(
        steady,
        meals_by_day=(3, 3, 0, 3, 3, 3, 3),
        kcal_by_day=(*steady.kcal_by_day[:2], 0.0, *steady.kcal_by_day[3:]),
        water_by_day=(2.4, 1.6, 0.0, 1.8, 1.5, 1.4, 2.5),
    )

    water = _card(generate_recap(stats), "grow", "water")

    assert water["note"].endswith("lowest on Sat.")


def test_a_tough_week_points_out_only_progress() -> None:
    # The tough week has one thin day (Saturday); so has the week before.
    worse = replace(
        sample_stats("tough"), last_week=_last_week(fiber_avg=20.0, thin_days=1)
    )
    better = replace(
        sample_stats("tough"), last_week=_last_week(fiber_avg=11.0, thin_days=1)
    )

    assert "trend" not in _card(generate_recap(worse), "grow", "fiber")
    assert _card(generate_recap(better), "grow", "fiber")["trend"]["good"] is True


@pytest.mark.parametrize(
    ("locale", "note", "title"),
    [
        (
            "en",
            "Most often: milk tea, about 1,100 kcal in all.",
            "Milk tea → unsweetened tea",
        ),
        (
            "vi",
            "Chủ yếu là trà sữa, khoảng 1.100 kcal cả tuần.",
            "Đổi trà sữa sang trà không đường",
        ),
        (
            "es",
            "Lo que más tomaste: té con leche, unas 1.100 kcal en total.",
            "Té con leche → té sin azúcar",
        ),
        (
            "de",
            "Am häufigsten: Milchtee, insgesamt etwa 1.100 kcal.",
            "Milchtee → ungesüßter Tee",
        ),
        ("ja", "主にミルクティーで、合計約1,100 kcal。", "ミルクティーを無糖のお茶に"),
    ],
)
def test_a_catalog_drink_is_named_in_the_users_language(locale, note, title) -> None:
    stats = replace(
        sample_stats("steady"),
        sugary_drinks=5,
        sugary_kcal=1100,
        top_sugary_drink="Milk tea",
    )

    payload = generate_recap(stats, RecapConfig(priorities={"sugary": 99}), locale)

    assert _card(payload, "grow", "fat")["note"] == note
    assert payload["plan"]["moves"][0]["title"] == title


@pytest.mark.parametrize("name", list(SAMPLES))
def test_the_stored_plan_ids_are_the_plan_the_recap_shows(name: str) -> None:
    stats = sample_stats(name)
    payload = generate_recap(stats, locale="vi")

    planned = planned_issue_ids(stats, payload["case_id"], CONFIG)

    moves = payload["plan"]["moves"]
    assert shown_plan(stats, payload["case_id"], CONFIG, moves, "vi") == planned
    assert len(planned) <= len(moves)


def test_the_french_weekend_note_ends_cleanly() -> None:
    stats = replace(
        sample_stats("tough"),
        kcal_by_day=(1880, 1850, 2150, 2300, 1800, 2700, 2500),
        weekend_delta=433,
        weekend_peaks=(PeakMeal(6, "Pizza", 1600),),
    )
    plain = replace(stats, weekend_peaks=())

    peak = _card(generate_recap(stats, locale="fr"), "grow", "calories")["note"]
    note = _card(generate_recap(plain, locale="fr"), "grow", "calories")["note"]

    assert ".." not in peak and ".." not in note
    assert peak[0].isupper() and note[0].isupper()


@pytest.mark.parametrize(("locale", "counters"), [("ja", "日杯"), ("zh", "个杯天")])
def test_counted_follow_units_start_with_a_counter(locale, counters) -> None:
    for issue_id in ("late_snack", "sugary", "weekend_over", "rest_under"):
        unit = TABLES[locale][f"follow.unit.{issue_id}"]
        assert unit[0] in counters, (issue_id, unit)


# --- Second review round ---------------------------------------------------


def test_coconut_water_is_named_as_a_drink() -> None:
    stats = replace(
        sample_stats("steady"),
        sugary_drinks=5,
        sugary_kcal=1100,
        top_sugary_drink="Coconut",
    )

    payload = generate_recap(stats, RecapConfig(priorities={"sugary": 99}))

    assert _card(payload, "grow", "fat")["note"].startswith("Most often: coconut water")


def test_late_snacks_logged_the_next_day_are_not_fewer_late_snacks() -> None:
    # The same seven days are logged, but Thursday to Sunday a day late:
    # those nights' snacks have no time of day, so the counts don't compare.
    stats = replace(
        sample_stats("steady"),
        late_snack_days=(),
        backdated_days=(3, 4, 5, 6),
        last_week=_last_week(plan=("late_snack",)),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_a_weekend_of_lone_breakfasts_is_not_a_fixed_weekend() -> None:
    steady = sample_stats("steady")
    stats = replace(
        steady,
        meals_by_day=(*steady.meals_by_day[:4], 1, 1, 1),
        kcal_by_day=(*steady.kcal_by_day[:4], 450.0, 450.0, 450.0),
        weekend_delta=0.0,
        last_week=_tough_last_week(),
    )

    payload = generate_recap(stats)

    assert all(card["icon"] != "check_circle" for card in _cards(payload, "wins"))


def test_water_compares_only_with_the_same_days_without_a_drink() -> None:
    # Last week was a four-day sign-up week with a drink every day; this
    # week logged drinks on four of seven days, so its average has three
    # empty days the other week doesn't.
    steady = sample_stats("steady")
    stats = replace(
        steady,
        water_by_day=(2.2, 2.2, 0.0, 2.2, 0.0, 2.2, 0.0),
        last_week=_last_week(week_days=4, water_days=4, water_avg=2.0),
    )

    water = _card(generate_recap(stats), "grow", "water")

    assert "trend" not in water


def test_a_plan_with_a_habit_never_reads_as_done_whole() -> None:
    stats = replace(
        sample_stats("steady"),
        late_snack_days=(4,),
        last_week=_last_week(plan=("late_snack",), plan_moves=2),
    )

    first = generate_recap(stats)["wins"]["cards"][0]

    assert first["title"] == "You hit one of last week's goals"


def test_a_rebuilt_plan_never_reads_as_done_whole() -> None:
    stats = replace(
        sample_stats("steady"),
        late_snack_days=(4,),
        last_week=_last_week(plan=("late_snack",), plan_exact=False),
    )

    first = generate_recap(stats)["wins"]["cards"][0]

    assert first["title"] == "You hit one of last week's goals"


def test_the_follow_through_card_keeps_the_share_tile_it_displaced() -> None:
    plain = generate_recap(sample_stats("perfect"))["share"]["stats"]
    last = last_week_of(sample_stats("perfect"), "perfect", CONFIG)
    followed = replace(
        sample_stats("perfect"),
        last_week=replace(last, late_snack_nights=4, plan=("late_snack",)),
    )

    tiles = generate_recap(followed)["share"]["stats"]

    assert tiles == plain


def test_the_fiber_win_names_days_only_for_most_of_the_week() -> None:
    # Four days logged, three of them at target: not most of the week.
    perfect = sample_stats("perfect")
    stats = replace(
        perfect,
        fiber_avg=27.0,
        fiber_by_day=(30.0, 30.0, 30.0, 20.0, 0.0, 0.0, 0.0),
        meals_by_day=(3, 3, 3, 3, 0, 0, 0),
    )

    assert _fiber_hit_card(stats)["note"] == "A solid daily average."


def test_a_water_fix_a_float_noisy_step_apart_counts() -> None:
    stats = replace(
        sample_stats("steady"),
        water_by_day=(2.3,) * 7,
        last_week=_last_week(water_avg=2.1, plan=("water_low",)),
    )

    first = generate_recap(stats)["wins"]["cards"][0]

    assert (first["icon"], first["unit"]) == ("check_circle", "water / day")


_MANY = RecapConfig(max_cards=8)


@pytest.mark.parametrize(
    ("sample", "section", "kind", "last", "expected"),
    [
        ("perfect", "wins", "water", {"water_avg": 3.1}, None),
        ("perfect", "wins", "water", {"water_avg": 2.1}, "Up from 2.1 L last week"),
        ("perfect", "wins", "fiber", {"fiber_avg": 25.0}, None),
        ("perfect", "wins", "fiber", {"fiber_avg": 19.0}, "Up from 19g last week"),
        ("steady", "wins", "calories", {"days_on_budget": 7}, None),
        ("steady", "wins", "move", {"workouts": 1, "week_days": 5}, None),
        (
            "steady",
            "grow",
            "fat",
            {"sugary_drinks": 7, "water_days": 5},
            None,
        ),
    ],
)
def test_trend_wiring(sample, section, kind, last, expected) -> None:
    stats = sample_stats(sample)
    if sample == "perfect":
        stats = replace(stats, fiber_avg=24.0, fiber_by_day=(24.0,) * 7)
    base = last_week_of(stats, sample, CONFIG)
    config = _MANY
    if kind == "fat":
        stats = replace(stats, sugary_drinks=5, sugary_kcal=1100)
        config = RecapConfig(max_cards=8, priorities={"sugary": 99})
    stats = replace(stats, last_week=replace(base, **last))

    card = _card(generate_recap(stats, config), section, kind)

    assert (card.get("trend") or {}).get("text") == expected
