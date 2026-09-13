"""Deterministic recap facts from a progress-summary window."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean
from typing import Any

from src.domain.services.progress_recap_fact_buckets import (
    best_bucket,
    best_day,
    calorie_cv,
    iso_week,
    weekend_gap,
    year_month,
)

HORIZONS = frozenset({"day", "week", "month", "year"})


@dataclass(frozen=True)
class RecapFacts:
    horizon: str
    start: str
    end: str
    total_days: int
    logged_days: int
    calories_sum: float
    target_sum: float
    balance_kcal: float
    calorie_avg: float
    target_avg: float
    protein_avg: float
    protein_target_avg: float
    protein_hit_days: int
    carbs_avg: float
    fat_avg: float
    fiber_avg: float
    fiber_target_avg: float
    sodium_avg: float | None
    sugar_avg: float | None
    hydration_avg: float
    hydration_goal_avg: float
    hydration_hit_days: int
    burn_total: float
    quality_avg: float | None
    best_day: str | None
    best_day_balance_kcal: float | None
    swing_cv: float | None
    weekend_gap_kcal: float | None
    best_week: str | None
    best_month: str | None

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)

    def stamp(self) -> str:
        return (
            f"{self.logged_days}-"
            f"{int(self.hydration_avg)}-"
            f"{int(self.protein_avg)}-"
            f"{int(self.calorie_avg)}-"
            f"{int(self.sodium_avg or 0)}"
        )


def build_recap_facts(
    days: list[dict[str, Any]],
    *,
    horizon: str,
    start: date,
    end: date,
) -> RecapFacts:
    logged = [row for row in days if _is_logged(row)]
    calories = [_num(row, "calories") for row in logged]
    targets = [_num(row, "target_calories") for row in logged]
    proteins = [_num(row, "protein_g") for row in logged]
    protein_targets = [_num(row, "protein_target_g") for row in logged]
    hydrations = [_num(row, "hydration_ml") for row in logged]
    hydration_goals = [_num(row, "hydration_goal_ml") for row in logged]
    sodiums = [_opt(row, "sodium_mg") for row in logged]
    sugars = [_opt(row, "added_sugar_g") for row in logged]
    quality_vals = [
        _num(row, "nrf_quality")
        for row in logged
        if int(row.get("nrf_coverage") or 0) > 0
    ]
    cal_sum = sum(calories)
    tgt_sum = sum(targets)
    best = best_day(logged)
    return RecapFacts(
        horizon=horizon,
        start=start.isoformat(),
        end=end.isoformat(),
        total_days=max((end - start).days + 1, 0),
        logged_days=len(logged),
        calories_sum=round(cal_sum, 1),
        target_sum=round(tgt_sum, 1),
        balance_kcal=round(cal_sum - tgt_sum, 1),
        calorie_avg=round(mean(calories), 1) if calories else 0.0,
        target_avg=round(mean(targets), 1) if targets else 0.0,
        protein_avg=round(mean(proteins), 1) if proteins else 0.0,
        protein_target_avg=round(mean(protein_targets), 1) if protein_targets else 0.0,
        protein_hit_days=sum(
            1
            for row in logged
            if _num(row, "protein_target_g") > 0
            and _num(row, "protein_g") >= 0.9 * _num(row, "protein_target_g")
        ),
        carbs_avg=_mean_key(logged, "carbs_g"),
        fat_avg=_mean_key(logged, "fat_g"),
        fiber_avg=_mean_key(logged, "fiber_g"),
        fiber_target_avg=_mean_key(logged, "fiber_target_g"),
        sodium_avg=_mean_opt(sodiums),
        sugar_avg=_mean_opt(sugars),
        hydration_avg=round(mean(hydrations), 1) if hydrations else 0.0,
        hydration_goal_avg=round(mean(hydration_goals), 1) if hydration_goals else 0.0,
        hydration_hit_days=sum(
            1
            for row in logged
            if _num(row, "hydration_goal_ml") > 0
            and _num(row, "hydration_ml") >= 0.9 * _num(row, "hydration_goal_ml")
        ),
        burn_total=round(sum(_num(row, "burned_calories") for row in logged), 1),
        quality_avg=round(mean(quality_vals), 2) if quality_vals else None,
        best_day=best[0],
        best_day_balance_kcal=best[1],
        swing_cv=calorie_cv(calories),
        weekend_gap_kcal=weekend_gap(logged),
        best_week=best_bucket(logged, iso_week),
        best_month=best_bucket(logged, year_month),
    )


def _is_logged(row: dict[str, Any]) -> bool:
    if int(row.get("meal_count") or 0) > 0:
        return True
    return str(row.get("logged_status") or "none") != "none"


def _num(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _opt(row: dict[str, Any], key: str) -> float | None:
    if row.get(key) is None:
        return None
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return None


def _mean_key(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(mean(_num(row, key) for row in rows), 1)


def _mean_opt(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(mean(present), 1)
