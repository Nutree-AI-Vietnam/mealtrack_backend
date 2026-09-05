"""Individual daily micronutrient goals from DRI tables.

Methodology: IOM (2000) Dietary Reference Intakes — Applications in Dietary
Assessment. The individual goal is the RDA (or AI / CDRR when no RDA exists).

Iron RDA: IOM 2001 micronutrients. Potassium AI and sodium CDRR: NASEM 2019.
Fiber AI: IOM 14 g / 1000 kcal. Added sugar: DGA <10% of energy, not a DRI.

NRF scoring still uses the FDA Daily Value table in ``nrf_score.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.services.fiber_target import fiber_daily_target_g

_DEFAULT_AGE = 30
_KCAL_PER_G_SUGAR = 4.0
_ADDED_SUGAR_ENERGY_FRACTION = 0.10
_ADDED_SUGAR_FALLBACK_G = 50.0


@dataclass(frozen=True)
class MicronutrientDailyTargets:
    iron_mg: float
    fiber_g: float
    potassium_mg: float
    sodium_mg: float
    added_sugar_g: float


def micronutrient_daily_targets(
    *,
    gender: str | None,
    age_years: int | None,
    plan_calories: float,
) -> MicronutrientDailyTargets:
    age = age_years if age_years and age_years > 0 else _DEFAULT_AGE
    male = _is_male(gender)
    return MicronutrientDailyTargets(
        iron_mg=_iron_rda_mg(male, age),
        fiber_g=fiber_daily_target_g(plan_calories),
        potassium_mg=_potassium_ai_mg(male, age),
        sodium_mg=_sodium_cdrr_mg(age),
        added_sugar_g=_added_sugar_limit_g(plan_calories),
    )


def _is_male(gender: str | None) -> bool:
    return (gender or "").strip().lower() in {"male", "m"}


def _iron_rda_mg(male: bool, age: int) -> float:
    if age <= 3:
        return 7.0
    if age <= 8:
        return 10.0
    if age <= 13:
        return 8.0
    if age <= 18:
        return 11.0 if male else 15.0
    if age <= 50:
        return 8.0 if male else 18.0
    return 8.0


def _potassium_ai_mg(male: bool, age: int) -> float:
    if age <= 3:
        return 2000.0
    if age <= 8:
        return 2300.0
    if age <= 13:
        return 2500.0 if male else 2300.0
    if age <= 18:
        return 3000.0 if male else 2300.0
    return 3400.0 if male else 2600.0


def _sodium_cdrr_mg(age: int) -> float:
    if age <= 3:
        return 1200.0
    if age <= 8:
        return 1500.0
    if age <= 13:
        return 1800.0
    return 2300.0


def _added_sugar_limit_g(plan_calories: float) -> float:
    if plan_calories <= 0:
        return _ADDED_SUGAR_FALLBACK_G
    return round(_ADDED_SUGAR_ENERGY_FRACTION * plan_calories / _KCAL_PER_G_SUGAR, 1)
