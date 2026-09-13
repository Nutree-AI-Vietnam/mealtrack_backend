from datetime import date

from src.domain.services.progress_recap_contract import fallback_recap
from src.domain.services.progress_recap_facts import build_recap_facts
from src.domain.services.progress_recap_health import pick_health_signal
from src.domain.services.progress_recap_labels import (
    friendly_day,
    friendly_month,
    friendly_week,
)


def _day(
    date_key: str,
    *,
    calories: float = 2000,
    target: float = 2000,
    protein: float = 130,
    fat: float = 60,
    fiber: float = 25,
    water: float = 2000,
) -> dict:
    return {
        "date": date_key,
        "calories": calories,
        "target_calories": target,
        "protein_g": protein,
        "protein_target_g": 130,
        "fat_g": fat,
        "fiber_g": fiber,
        "fiber_target_g": 25,
        "hydration_ml": water,
        "hydration_goal_ml": 2000,
        "meal_count": 3,
        "logged_status": "full",
    }


def _facts(days: list[dict], horizon: str = "week"):
    return build_recap_facts(
        days,
        horizon=horizon,
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    )


def test_low_protein_wins_over_unused_weekly_budget() -> None:
    facts = _facts(
        [
            _day("2026-09-07", protein=80),
            _day("2026-09-08", protein=70),
        ]
    )
    assert facts.balance_kcal < 0 or facts.logged_days < facts.total_days
    assert pick_health_signal(facts).key == "protein_low"
    headline = fallback_recap(facts)["headline"]
    assert "Protein" in headline
    assert "under the week" not in headline


def test_high_fat_headline_names_fat() -> None:
    facts = _facts([_day("2026-09-07", calories=2000, fat=100)])
    assert pick_health_signal(facts).key == "fat_high"
    assert "Fat" in fallback_recap(facts)["headline"]


def test_high_sodium_headline_skips_sodium_beat() -> None:
    facts = _facts([_day("2026-09-07") | {"sodium_mg": 3100}])
    assert pick_health_signal(facts).key == "salt_high"
    payload = fallback_recap(facts)
    assert "Sodium" in payload["headline"]
    assert "3100" in payload["headline"]
    assert "sodium" not in payload["body"].lower()
    assert "3100" not in payload["body"]
    assert all(item["kind"] != "sodium" for item in payload["highlights"])


def test_sodium_warning_stays_in_highlight_when_protein_is_headline() -> None:
    facts = _facts([_day("2026-09-07", protein=80) | {"sodium_mg": 3100}])
    assert pick_health_signal(facts).key == "protein_low"
    payload = fallback_recap(facts)
    assert "Protein" in payload["headline"]
    assert "50" in payload["headline"]
    assert "3100" not in payload["body"]
    assert any(
        item["kind"] == "sodium" and "3100" in item["detail"]
        for item in payload["highlights"]
    )
    assert all(
        item["kind"] not in {"protein", "protein_hits"}
        for item in payload["highlights"]
    )


def test_week_next_move_is_a_habit_not_the_next_meal() -> None:
    facts = _facts([_day("2026-09-07", protein=80)])
    payload = fallback_recap(facts)
    assert "next meal" not in payload["next_move"].lower()
    assert "on the days you log" in payload["next_move"]
    vi = fallback_recap(facts, locale="vi")
    assert "Bữa tới" not in vi["next_move"]
    assert "ở các ngày bạn ghi" in vi["next_move"]


def test_friendly_labels_skip_iso_and_week_numbers() -> None:
    assert "2026-09-08" not in friendly_day("2026-09-08", "en")
    assert friendly_day("2026-09-08", "en") == "Tuesday, 8 September"
    assert "W36" not in friendly_week("2026-W36", "en")
    assert friendly_week("2026-W36", "en") == "Week of Monday, 31 August"
    assert friendly_day("2026-09-08", "vi") == "Thứ Ba, 8 tháng 9"
    assert friendly_month("2026-09", "vi") == "Tháng 9, 2026"


def test_zero_water_is_watch_even_when_protein_is_headline() -> None:
    facts = _facts([_day("2026-09-07", protein=80, water=0)], horizon="day")
    assert pick_health_signal(facts).key == "protein_low"
    payload = fallback_recap(facts)
    water = next(item for item in payload["highlights"] if item["kind"] == "hydration")
    assert water["polarity"] == "watch"
    assert "0" in water["detail"]


def test_week_zero_water_is_watch_when_another_signal_leads() -> None:
    facts = _facts([_day("2026-09-07", protein=80, water=0)])
    assert pick_health_signal(facts).key == "protein_low"
    payload = fallback_recap(facts)
    water = next(item for item in payload["highlights"] if item["kind"] == "hydration")
    assert water["polarity"] == "watch"


def test_day_beats_do_not_repeat_headline_or_next_move() -> None:
    facts = _facts([_day("2026-09-07", protein=80)], horizon="day")
    payload = fallback_recap(facts)
    kinds = [item["kind"] for item in payload["highlights"]]
    assert "protein" not in kinds
    joined = " ".join(item["title"] + " " + item["detail"] for item in payload["highlights"])
    assert "Tonight" not in joined
    assert "Tối nay" not in joined
    vi = fallback_recap(facts, locale="vi")
    vi_joined = " ".join(item["title"] + " " + item["detail"] for item in vi["highlights"])
    assert "Tối nay" not in vi_joined
    assert vi["next_move"] == "Thêm đạm tối nay."
