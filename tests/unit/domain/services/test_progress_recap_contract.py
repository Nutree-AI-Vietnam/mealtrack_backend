from datetime import date

from src.domain.services.progress_recap_contract import (
    fallback_recap,
    parse_ai_recap,
)
from src.domain.services.progress_recap_facts import build_recap_facts


def _facts():
    return build_recap_facts(
        [
            {
                "date": "2026-09-07",
                "calories": 2100,
                "target_calories": 2000,
                "protein_g": 140,
                "protein_target_g": 130,
                "hydration_ml": 2000,
                "hydration_goal_ml": 2000,
                "burned_calories": 0,
                "meal_count": 3,
                "logged_status": "full",
            }
        ],
        horizon="week",
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    )


def test_parse_ai_recap_keeps_horizon_kinds_only() -> None:
    payload = parse_ai_recap(
        {
            "headline": "Week stayed close.",
            "body": "Two logged days.",
            "next_move": "Keep protein first.",
            "highlights": [
                {
                    "kind": "pace",
                    "polarity": "watch",
                    "title": "Pace",
                    "detail": "+100 kcal",
                },
                {
                    "kind": "best_day",
                    "polarity": "win",
                    "title": "Best day",
                    "detail": "Sep 7",
                },
                {
                    "kind": "quality",
                    "polarity": "win",
                    "title": "Quality",
                    "detail": "not allowed for week",
                },
            ],
        },
        _facts(),
    )
    assert payload is not None
    kinds = [item["kind"] for item in payload["highlights"]]
    assert "quality" not in kinds
    assert kinds == ["pace", "best_day"]


def test_ai_hydration_win_at_zero_ml_becomes_watch() -> None:
    facts = build_recap_facts(
        [
            {
                "date": "2026-09-07",
                "calories": 2000,
                "target_calories": 2000,
                "protein_g": 130,
                "protein_target_g": 130,
                "hydration_ml": 0,
                "hydration_goal_ml": 2000,
                "meal_count": 3,
                "logged_status": "full",
            }
        ],
        horizon="week",
        start=date(2026, 9, 7),
        end=date(2026, 9, 13),
    )
    payload = parse_ai_recap(
        {
            "headline": "Protein looks fine.",
            "body": "From 1 of 7 days you logged.",
            "next_move": "Drink water on the days you log.",
            "highlights": [
                {
                    "kind": "hydration",
                    "polarity": "win",
                    "title": "Water",
                    "detail": "0 ml of 2000 ml.",
                },
                {
                    "kind": "best_day",
                    "polarity": "win",
                    "title": "Closest day",
                    "detail": "Monday",
                },
            ],
        },
        facts,
    )
    assert payload is not None
    water = next(item for item in payload["highlights"] if item["kind"] == "hydration")
    assert water["polarity"] == "watch"


def test_fallback_recap_uses_fact_numbers() -> None:
    payload = fallback_recap(_facts())
    assert payload["status"] == "ready"
    assert "140" in payload["headline"] or "balanced" in payload["headline"]
    assert len(payload["highlights"]) == 3


def test_fallback_recap_uses_vietnamese() -> None:
    payload = fallback_recap(_facts(), locale="vi-VN")
    assert payload["status"] == "ready"
    assert "over the" not in payload["headline"]
    assert "Thiếu 100" not in payload["headline"]
    assert any("Đạm" in item["title"] for item in payload["highlights"])


def test_fallback_recap_kinds_match_each_horizon() -> None:
    from src.domain.services.progress_recap_prompt import ALLOWED_KINDS

    days = [
        {
            "date": "2026-09-07",
            "calories": 2100,
            "target_calories": 2000,
            "protein_g": 140,
            "protein_target_g": 130,
            "hydration_ml": 2000,
            "hydration_goal_ml": 2000,
            "burned_calories": 0,
            "meal_count": 3,
            "logged_status": "full",
            "nrf_quality": 0.7,
            "nrf_coverage": 2,
        }
    ]
    seen: set[tuple[str, ...]] = set()
    for horizon, start, end in (
        ("day", date(2026, 9, 7), date(2026, 9, 7)),
        ("week", date(2026, 9, 7), date(2026, 9, 13)),
        ("month", date(2026, 9, 1), date(2026, 9, 30)),
        ("year", date(2026, 1, 1), date(2026, 12, 31)),
    ):
        facts = build_recap_facts(days, horizon=horizon, start=start, end=end)
        payload = fallback_recap(facts)
        kinds = tuple(item["kind"] for item in payload["highlights"])
        assert kinds
        assert all(kind in ALLOWED_KINDS[horizon] for kind in kinds)
        if horizon == "day":
            assert "today's meals" in payload["body"].lower()
        else:
            assert (
                f"{facts.logged_days} of {facts.total_days} days you logged"
                in payload["body"]
            )
        assert "next meal" not in payload["next_move"].lower()
        seen.add(kinds)
    assert len(seen) == 4
