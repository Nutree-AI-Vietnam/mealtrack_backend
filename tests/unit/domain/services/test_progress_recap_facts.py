from datetime import date

from src.domain.services.progress_recap_facts import build_recap_facts


def _day(
    date_key: str,
    calories: float,
    target: float,
    protein: float = 120,
    fat: float = 60,
) -> dict:
    return {
        "date": date_key,
        "calories": calories,
        "target_calories": target,
        "protein_g": protein,
        "protein_target_g": 130,
        "carbs_g": 180,
        "fat_g": fat,
        "fiber_g": 18,
        "fiber_target_g": 25,
        "hydration_ml": 1800,
        "hydration_goal_ml": 2000,
        "burned_calories": 200,
        "meal_count": 3,
        "logged_status": "full",
        "nrf_quality": 0.7,
        "nrf_coverage": 2,
    }


def test_week_facts_count_logged_days_and_balance() -> None:
    facts = build_recap_facts(
        [
            _day("2026-09-07", 2200, 2000),
            _day("2026-09-08", 1900, 2000, protein=80),
            {"date": "2026-09-09", "meal_count": 0, "logged_status": "none"},
        ],
        horizon="week",
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    )
    assert facts.logged_days == 2
    assert facts.total_days == 7
    assert facts.balance_kcal == 100.0
    assert facts.protein_hit_days == 1
    assert facts.best_day == "2026-09-08"
    assert facts.sodium_avg is None
    assert facts.sugar_avg is None


def test_month_facts_include_weekend_gap() -> None:
    days = [
        _day("2026-09-07", 1800, 2000),  # Monday
        _day("2026-09-08", 1800, 2000),
        _day("2026-09-12", 2400, 2000),  # Saturday
        _day("2026-09-13", 2400, 2000),  # Sunday
    ]
    facts = build_recap_facts(
        days,
        horizon="month",
        start=date(2026, 9, 1),
        end=date(2026, 9, 13),
    )
    assert facts.weekend_gap_kcal == 600.0
    assert facts.best_week is not None


def test_facts_average_sodium_and_sugar() -> None:
    facts = build_recap_facts(
        [
            _day("2026-09-07", 2000, 2000)
            | {"sodium_mg": 2000, "added_sugar_g": 40},
            _day("2026-09-08", 2000, 2000)
            | {"sodium_mg": 2600, "added_sugar_g": 60},
        ],
        horizon="week",
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    )
    assert facts.sodium_avg == 2300.0
    assert facts.sugar_avg == 50.0
    assert facts.stamp() != build_recap_facts(
        [
            _day("2026-09-07", 2000, 2000)
            | {"hydration_ml": 400},
        ],
        horizon="week",
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    ).stamp()
