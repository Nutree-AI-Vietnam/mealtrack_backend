"""Advice without a card: reminders, streak goals and challenges.

``HABITS`` fill the plan when the week's issues leave room. ``CHALLENGES``
are for a perfect week; the first one that applies is used. Both keep the
design engine's order.
"""

from __future__ import annotations

from collections.abc import Callable

from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.stats_math import meal_for_hour
from src.domain.services.weekly_recap.types import Habit, Move


def _fixed(key: str, kind: str, icon: str) -> Callable[[RecapContext], Move]:
    """Advice whose copy has no placeholders."""

    def advice(ctx: RecapContext) -> Move:
        return Move(
            kind,
            icon,
            ctx.t(f"{key}.advice"),
            ctx.t(f"{key}.impact"),
            ctx.t(f"{key}.why"),
            ctx.t(f"{key}.short"),
        )

    return advice


def _log_meal(ctx: RecapContext) -> bool:
    missed = ctx.stats.missed_meal
    return missed is not None and missed.days >= ctx.config.missed_meal_days


def _log_meal_advice(ctx: RecapContext) -> Move:
    missed = ctx.stats.missed_meal
    meal_type = missed.meal_type if missed is not None else None
    meal = ctx.meal(meal_type)
    return Move(
        "fat",
        "wb_sunny" if meal_type == "breakfast" else "restaurant",
        ctx.t("log_meal.advice", meal=meal),
        ctx.t("log_meal.impact"),
        ctx.t(
            "log_meal.why",
            meal=meal,
            n=missed.days if missed is not None else 0,
            d=ctx.facts.week_days,
        ),
        ctx.t("log_meal.short", meal=meal),
    )


def _remind(ctx: RecapContext) -> bool:
    missed = ctx.stats.missed_meal
    has_reminder = missed is not None and missed.remind_at is not None
    return has_reminder or ctx.stats.common_log_hour is not None


def _remind_advice(ctx: RecapContext) -> Move:
    # A reminder for the meal most often forgotten, at the user's time for
    # it. Weeks stored before reminder times were kept, or with no missed
    # meal, remind at the hour the user usually logs.
    stats, missed = ctx.stats, ctx.stats.missed_meal
    if missed is not None and missed.remind_at is not None:
        meal, at = ctx.meal(missed.meal_type), ctx.time(missed.remind_at)
        why = ctx.t("remind.why.missed")
    else:
        hour = stats.common_log_hour or 0
        meal = ctx.meal(stats.common_meal or meal_for_hour(hour))
        at, why = ctx.hour(hour), ctx.t("remind.why")
    return Move(
        "carbs",
        "notifications_active",
        ctx.t("remind.advice", meal=meal, time=at),
        ctx.t("remind.impact"),
        why,
        ctx.t("remind.short"),
    )


def _streak7(ctx: RecapContext) -> bool:
    config = ctx.config
    return config.new_streak_min <= ctx.stats.streak < config.streak_goal


def _streak7_advice(ctx: RecapContext) -> Move:
    streak, goal = ctx.stats.streak, ctx.config.streak_goal
    left = max(goal - streak, 0)
    return Move(
        "fat",
        "local_fire_department",
        ctx.t("streak7.advice", goal=goal),
        ctx.t("streak7.impact", count=left, n=left),
        ctx.t("streak7.why", n=streak),
        ctx.t("streak7.short", goal=goal),
    )


def _one_meal(ctx: RecapContext) -> bool:
    facts, config = ctx.facts, ctx.config
    missed_days = facts.week_days - facts.days_logged
    return (
        missed_days >= config.one_meal_missed_days
        or ctx.stats.gap_before >= config.comeback_gap_days
    )


def _fiber_challenge(ctx: RecapContext) -> bool:
    stats = ctx.stats
    target = stats.fiber_target
    ceiling = target * ctx.config.fiber_challenge_ratio
    return target > 0 and target <= stats.fiber_avg < ceiling


def _fiber_challenge_advice(ctx: RecapContext) -> Move:
    step = ctx.config.fiber_challenge_step
    goal = ctx.num(ctx.stats.fiber_target + step)
    return Move(
        "fiber",
        "eco",
        ctx.t("challenge.fiber.advice", t=goal),
        ctx.t("challenge.fiber.impact", n=ctx.num(step)),
        ctx.t("challenge.fiber.why"),
        ctx.t("challenge.fiber.short", t=goal),
    )


def _move_challenge_advice(ctx: RecapContext) -> Move:
    sessions = len(ctx.stats.workouts) + 1
    return Move(
        "move",
        "directions_run",
        ctx.t("challenge.move.advice"),
        ctx.t("challenge.move.impact", count=sessions, n=sessions),
        ctx.t("challenge.move.why"),
        ctx.t("challenge.move.short"),
    )


HABITS: tuple[Habit, ...] = (
    Habit("log_meal", _log_meal, _log_meal_advice),
    Habit("remind", _remind, _remind_advice),
    Habit(
        "photo",
        lambda ctx: ctx.stats.photo_share < ctx.config.photo_share_max,
        _fixed("photo", "carbs", "photo_camera"),
    ),
    Habit(
        "water_remind",
        lambda ctx: not ctx.facts.water_tracked,
        _fixed("water_remind", "water", "notifications"),
    ),
    Habit("streak7", _streak7, _streak7_advice),
    Habit("one_meal", _one_meal, _fixed("one_meal", "calm", "restaurant")),
)

CHALLENGES: tuple[Habit, ...] = (
    Habit("challenge_fiber", _fiber_challenge, _fiber_challenge_advice),
    Habit("challenge_move", lambda ctx: True, _move_challenge_advice),
)
