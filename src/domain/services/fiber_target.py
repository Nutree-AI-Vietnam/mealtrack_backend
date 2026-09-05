"""Daily fiber target used by the Nutrition Score (IOM density, WHO floor)."""

FIBER_G_PER_1000_KCAL = 14.0
FIBER_G_DAILY_FLOOR = 25.0


def fiber_daily_target_g(target_calories: float) -> float:
    if target_calories <= 0:
        return FIBER_G_DAILY_FLOOR
    density = FIBER_G_PER_1000_KCAL * target_calories / 1000.0
    return round(max(FIBER_G_DAILY_FLOOR, density), 1)
