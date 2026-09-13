"""Pick one health signal users can act on. Unused weekly budget is ignored."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.services.progress_recap_facts import RecapFacts

_FAT_SHARE_HIGH = 0.38
_PROTEIN_LOW = 0.9
_PROTEIN_HIGH = 1.25
_FIBER_LOW = 0.8
_WATER_LOW = 0.85
_CAL_OVER = 1.05
_CAL_UNDER = 0.85
_SODIUM_HIGH = 2300.0
_SUGAR_HIGH = 50.0


@dataclass(frozen=True)
class HealthSignal:
    key: str
    kind: str
    polarity: str


def pick_health_signal(facts: RecapFacts) -> HealthSignal:
    protein_ratio = _ratio(facts.protein_avg, facts.protein_target_avg)
    calorie_ratio = _ratio(facts.calorie_avg, facts.target_avg)
    fiber_ratio = _ratio(facts.fiber_avg, facts.fiber_target_avg)
    if protein_ratio is not None and protein_ratio < _PROTEIN_LOW:
        return HealthSignal("protein_low", "protein", "watch")
    if fat_is_high(facts):
        return HealthSignal("fat_high", "fat", "watch")
    if sodium_is_high(facts):
        return HealthSignal("salt_high", "sodium", "watch")
    if sugar_is_high(facts):
        return HealthSignal("sugar_high", "quality", "watch")
    if protein_ratio is not None and protein_ratio > _PROTEIN_HIGH:
        return HealthSignal("protein_high", "protein", "watch")
    if calorie_ratio is not None and calorie_ratio > _CAL_OVER:
        return HealthSignal("logged_over", "pace", "watch")
    if water_is_low(facts):
        return HealthSignal("water_low", "hydration", "watch")
    if fiber_ratio is not None and fiber_ratio < _FIBER_LOW:
        return HealthSignal("fiber_low", "quality", "watch")
    if calorie_ratio is not None and calorie_ratio < _CAL_UNDER:
        return HealthSignal("logged_under", "pace", "watch")
    return HealthSignal("on_track", "pace", "win")


def water_is_low(facts: RecapFacts) -> bool:
    ratio = _ratio(facts.hydration_avg, facts.hydration_goal_avg)
    return ratio is not None and ratio < _WATER_LOW


def fat_is_high(facts: RecapFacts) -> bool:
    share = _fat_share(facts)
    return share is not None and share >= _FAT_SHARE_HIGH


def sodium_is_high(facts: RecapFacts) -> bool:
    return facts.sodium_avg is not None and facts.sodium_avg >= _SODIUM_HIGH


def sugar_is_high(facts: RecapFacts) -> bool:
    return facts.sugar_avg is not None and facts.sugar_avg >= _SUGAR_HIGH


def protein_gap(facts: RecapFacts) -> float:
    return max(facts.protein_target_avg - facts.protein_avg, 0.0)


def protein_over(facts: RecapFacts) -> float:
    return max(facts.protein_avg - facts.protein_target_avg, 0.0)


def fat_calorie_share(facts: RecapFacts) -> float:
    return _fat_share(facts) or 0.0


def _fat_share(facts: RecapFacts) -> float | None:
    if facts.calorie_avg <= 0 or facts.fat_avg <= 0:
        return None
    return round(facts.fat_avg * 9 / facts.calorie_avg, 3)


def _ratio(actual: float, target: float) -> float | None:
    if target <= 0:
        return None
    return actual / target
