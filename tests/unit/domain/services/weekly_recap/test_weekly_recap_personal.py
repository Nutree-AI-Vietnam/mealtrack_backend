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
from src.domain.services.weekly_recap.last_week import last_week_of
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
    assert sugary["note"] == "Mostly Trà sữa trân châu, about 1,100 kcal in all."
    assert payload["plan"]["moves"][0]["title"] == (
        "Swap Trà sữa trân châu for unsweetened tea"
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


def test_counts_only_compare_weeks_of_the_same_length() -> None:
    # Last week was a five-day first week: its days on budget and late-snack
    # nights don't compare, its averages do.
    last = _last_week(week_days=5, days_on_budget=2, late_snack_nights=1)
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
    # Fiber is still low, so the plan's second item, late snacks, is the win.
    payload = generate_recap(_followed_week(plan=("fiber_low", "late_snack")))

    assert payload["wins"]["cards"][0]["unit"] == "late-snack night"


def test_an_issue_that_still_holds_is_no_win() -> None:
    stats = replace(sample_stats("steady"), last_week=_last_week(plan=("late_snack",)))

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
