"""Win cards about the week's rhythm: training, water, logging and streaks."""

from __future__ import annotations

from src.domain.services.weekly_recap.compare import WATER, WORKOUTS, trend
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.types import Card, Insight
from src.domain.services.weekly_recap.viz import card, dots


def meals_stat(ctx: RecapContext) -> list[str]:
    """Share-card tile for meals logged out of meals expected."""
    facts = ctx.facts
    return [f"{facts.meals_logged}/{facts.meals_expected}", ctx.t("stat.meals")]


def streak_stat(ctx: RecapContext) -> list[str]:
    """Share-card tile for the current logging streak."""
    streak = ctx.stats.streak
    return [str(streak), ctx.t("stat.streak", count=streak)]


def _workouts_stat(ctx: RecapContext) -> list[str]:
    sessions = len(ctx.stats.workouts)
    return [str(sessions), ctx.t("stat.workouts", count=sessions)]


def _moving_card(ctx: RecapContext) -> Card:
    stats, facts = ctx.stats, ctx.facts
    if stats.goal == "bulk" and facts.strength_count >= ctx.config.moving_workouts:
        lifts = [workout for workout in stats.workouts if workout.strength]
        return card(
            "move",
            "fitness_center",
            ctx.t("moving.title.strength"),
            count=facts.strength_count,
            unit=ctx.t("moving.unit.strength", count=facts.strength_count),
            note=ctx.t(
                "moving.note.strength",
                kcal=ctx.num(sum(workout.kcal for workout in lifts)),
                min=ctx.num(sum(workout.minutes for workout in lifts)),
            ),
            viz=dots(facts.strength_days),
        )
    sessions = len(stats.workouts)
    return card(
        "move",
        "directions_run",
        ctx.t("moving.title"),
        count=sum(workout.kcal for workout in stats.workouts),
        prefix="+",
        unit=ctx.t("moving.unit"),
        note=ctx.t(
            "moving.note",
            count=sessions,
            n=sessions,
            min=ctx.num(sum(workout.minutes for workout in stats.workouts)),
        ),
        viz=dots(facts.workout_days),
        trend=trend(ctx, WORKOUTS, good_only=True),
    )


def _water_hit_card(ctx: RecapContext) -> Card:
    facts = ctx.facts
    return card(
        "water",
        "water_drop",
        ctx.t("water_hit.title"),
        value=ctx.t("card.value.litres", v=ctx.dec(facts.water_avg)),
        unit=ctx.t("card.unit.per_day_avg"),
        note=ctx.t("water_hit.note", n=facts.water_days_hit, d=facts.week_days),
        viz=dots(facts.water_hit_days),
        trend=trend(ctx, WATER, good_only=True),
    )


def _logged_daily_card(ctx: RecapContext) -> Card:
    facts = ctx.facts
    return card(
        "calm",
        "edit_note",
        ctx.t("logged_daily.title"),
        count=facts.week_days,
        unit=ctx.t("logged_daily.unit", d=facts.week_days),
        note=ctx.t("logged_daily.note"),
        viz=dots([day for day in range(7) if facts.logged[day]]),
    )


def _new_streak(ctx: RecapContext) -> bool:
    stats, config = ctx.stats, ctx.config
    return (
        stats.gap_before >= config.new_streak_gap
        and stats.streak >= config.new_streak_min
    )


def _new_streak_card(ctx: RecapContext) -> Card:
    streak, goal = ctx.stats.streak, ctx.config.streak_goal
    if streak >= goal:
        note = ctx.t("new_streak.note.reached", goal=goal)
    else:
        note = ctx.t("new_streak.note", count=goal - streak, n=goal - streak, goal=goal)
    return card(
        "fat",
        "local_fire_department",
        ctx.t("new_streak.title"),
        count=streak,
        unit=ctx.t("new_streak.unit", count=streak),
        note=note,
        viz=dots([6 - offset for offset in range(min(streak, 7))]),
    )


MOVING = Insight(
    "moving",
    80,
    lambda ctx: len(ctx.stats.workouts) >= ctx.config.moving_workouts,
    _moving_card,
    _workouts_stat,
)
WATER_HIT = Insight(
    "water_hit",
    70,
    lambda ctx: ctx.facts.water_days_hit >= ctx.config.water_hit_days,
    _water_hit_card,
    lambda ctx: [
        f"{ctx.facts.water_days_hit}/{ctx.facts.week_days}",
        ctx.t("stat.water"),
    ],
)
LOGGED_DAILY = Insight(
    "logged_daily",
    40,
    lambda ctx: ctx.facts.days_logged == ctx.facts.week_days,
    _logged_daily_card,
    meals_stat,
)
NEW_STREAK = Insight("new_streak", 60, _new_streak, _new_streak_card, streak_stat)
